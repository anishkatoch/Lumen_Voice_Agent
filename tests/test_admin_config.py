"""
Tests for admin-portal exposure of model/deployment names and call retry
settings that previously lived only in env vars / config.yaml, invisible to
and unchangeable from the admin portal (app/admin/admin_config_service.py,
app/routers/admin.py). Also covers the specific bug this was fixing: Azure's
chat deployment (the real primary model when Azure OpenAI is configured,
per this repo's Azure-first convention) was read straight from the
AZURE_OPENAI_DEPLOYMENT env var in app/voice/llm_service.py, so changing
"Model name" in the Providers tab silently had no effect on production calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

pytestmark = pytest.mark.asyncio

from tests.conftest import make_token

ADMIN_EMAIL = "katochaneesh@gmail.com"

NEW_MODEL_KEYS = [
    "azure_openai_deployment", "azure_openai_embedding_deployment", "azure_openai_transcribe_deployment",
    "stt_model", "tts_elevenlabs_model_id", "azure_speech_voice", "realtime_model",
]
NEW_RETRY_KEYS = ["call_retry_interval_minutes", "call_retry_max_attempts"]


def admin_token() -> str:
    import time, jwt, os
    secret = os.environ["SUPABASE_JWT_SECRET"]
    now = int(time.time())
    payload = {"sub": "admin-user-id", "email": ADMIN_EMAIL, "role": "authenticated", "iat": now, "exp": now + 3600}
    return jwt.encode(payload, secret, algorithm="HS256")


def _admin_auth():
    return {"Authorization": f"Bearer {admin_token()}"}


def _user_auth():
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ── admin_config_service: DEFAULTS / get / save ─────────────────────────────

class TestAdminConfigDefaults:
    def test_new_model_keys_default_to_empty_string(self):
        """Empty = "use the env/config.yaml default", same convention as the
        pre-existing tts_elevenlabs_voice_id key - nothing changes in
        production until an admin explicitly sets one of these."""
        from app.admin.admin_config_service import DEFAULTS
        for key in NEW_MODEL_KEYS:
            assert DEFAULTS[key] == ""

    def test_new_retry_keys_default_to_3_and_5(self):
        from app.admin.admin_config_service import DEFAULTS
        assert DEFAULTS["call_retry_interval_minutes"] == "3"
        assert DEFAULTS["call_retry_max_attempts"] == "5"

    def test_get_all_falls_back_to_defaults_on_db_error(self, mocker):
        mocker.patch("app.admin.admin_config_service.get_supabase", side_effect=RuntimeError("db down"))
        import app.admin.admin_config_service as acs
        acs._cache = {}
        acs._cache_time = 0.0
        cfg = acs.get_all()
        for key in NEW_MODEL_KEYS + NEW_RETRY_KEYS:
            assert key in cfg

    def test_save_updates_cache_immediately(self, mocker):
        mock_db = MagicMock()
        mocker.patch("app.admin.admin_config_service.get_supabase", return_value=mock_db)
        import app.admin.admin_config_service as acs
        acs._cache = dict(acs.DEFAULTS)
        acs._cache_time = __import__("time").monotonic()

        acs.save({"azure_openai_deployment": "gpt-4.1"})

        assert acs.get("azure_openai_deployment") == "gpt-4.1"


# ── PUT /api/admin/config whitelist ─────────────────────────────────────────

class TestAdminConfigRoute:
    async def test_put_accepts_new_model_and_retry_keys(self, client, mocker):
        mock_save = mocker.patch("app.admin.admin_config_service.save")
        mocker.patch("app.admin.admin_config_service.get_all", return_value={"ok": True})
        body = {k: "custom-value" for k in NEW_MODEL_KEYS}
        body.update({"call_retry_interval_minutes": "7", "call_retry_max_attempts": "3"})

        resp = await client.put("/api/admin/config", headers=_admin_auth(), json=body)

        assert resp.status_code == 200
        saved = mock_save.call_args.args[0]
        for key in NEW_MODEL_KEYS + NEW_RETRY_KEYS:
            assert key in saved

    async def test_put_rejects_unknown_key(self, client, mocker):
        mocker.patch("app.admin.admin_config_service.save")
        mocker.patch("app.admin.admin_config_service.get_all", return_value={})

        resp = await client.put("/api/admin/config", headers=_admin_auth(), json={"not_a_real_key": "x"})

        assert resp.status_code == 400

    async def test_put_requires_admin(self, client):
        resp = await client.put("/api/admin/config", headers=_user_auth(), json={"llm_model": "gpt-4o"})
        assert resp.status_code == 403


# ── The actual bug: Azure deployment override must reach llm_service ───────

class TestAzureDeploymentOverride:
    def test_augment_with_azure_uses_admin_override_over_env_default(self, mocker):
        from app.voice import llm_service

        mock_azure_client = MagicMock()
        mocker.patch.object(llm_service, "_get_azure_openai", return_value=mock_azure_client)
        mocker.patch(
            "app.admin.admin_config_service.get",
            side_effect=lambda k: "gpt-4.1-admin-override" if k == "azure_openai_deployment" else "",
        )

        chain = llm_service._augment_with_azure(MagicMock(), "openai", "gpt-4o-mini")

        azure_entry = next(c for c in chain if c[1] == "azure_openai")
        assert azure_entry[2] == "gpt-4.1-admin-override"

    def test_augment_with_azure_falls_back_to_env_default_when_unset(self, mocker):
        from app.voice import llm_service

        mock_azure_client = MagicMock()
        mocker.patch.object(llm_service, "_get_azure_openai", return_value=mock_azure_client)
        mocker.patch("app.admin.admin_config_service.get", return_value="")

        chain = llm_service._augment_with_azure(MagicMock(), "openai", "gpt-4o-mini")

        azure_entry = next(c for c in chain if c[1] == "azure_openai")
        assert azure_entry[2] == llm_service.AZURE_OPENAI_DEPLOYMENT

    def test_augment_with_azure_untouched_for_non_openai_providers(self, mocker):
        """groq/anthropic/ollama bypass Azure entirely - no admin_config
        lookup should even happen for them."""
        from app.voice import llm_service

        mock_get_config = mocker.patch("app.admin.admin_config_service.get")
        client = MagicMock()

        chain = llm_service._augment_with_azure(client, "groq", "llama-3.1-70b")

        assert chain == [(client, "groq", "llama-3.1-70b")]
        mock_get_config.assert_not_called()
