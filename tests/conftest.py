"""
Shared fixtures for all tests.

Strategy:
- Every external call (Supabase, OpenAI, Deepgram, ElevenLabs) is mocked.
- The real FastAPI app is used via httpx.AsyncClient + ASGITransport.
- A valid-looking JWT is injected so auth middleware passes without hitting Supabase.
"""
import pytest
import jwt
import time
import os

# ── set env vars BEFORE any app module is imported ───────────────────
os.environ["DATABASE_URL"]               = "postgresql://fake:fake@localhost/fake"
os.environ["SUPABASE_URL"]               = "https://fake.supabase.co"
os.environ["SUPABASE_ANON_KEY"]          = "fake-anon-key"
os.environ["SUPABASE_SERVICE_ROLE_KEY"]  = "fake-service-role-key"
os.environ["SUPABASE_JWT_SECRET"]        = "test-jwt-secret-that-is-long-enough-32chars"
os.environ["OPENAI_API_KEY"]             = "sk-fake-openai"
os.environ["DEEPGRAM_API_KEY"]           = "fake-deepgram"
os.environ["ELEVENLABS_API_KEY"]         = "fake-elevenlabs"
os.environ["WEBHOOK_SECRET"]             = ""   # disabled by default in tests

JWT_SECRET    = os.environ["SUPABASE_JWT_SECRET"]
TEST_USER_ID  = "11111111-1111-1111-1111-111111111111"
TEST_CONV_ID  = "22222222-2222-2222-2222-222222222222"


def make_token(user_id: str = TEST_USER_ID, expired: bool = False) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "email": "test@example.com",
        "role": "authenticated",
        "iat": now,
        "exp": (now - 10) if expired else (now + 3600),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def valid_token() -> str:
    return make_token()


@pytest.fixture
def expired_token() -> str:
    return make_token(expired=True)


@pytest.fixture
def auth_headers(valid_token) -> dict:
    return {"Authorization": f"Bearer {valid_token}"}


@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _make_mock_db(mocker):
    """Build a properly structured MagicMock for the Supabase client."""
    mock_db = mocker.MagicMock()

    # conversations table
    conv_table = mocker.MagicMock()
    conv_table.insert.return_value.execute.return_value.data = [
        {"id": TEST_CONV_ID, "user_id": TEST_USER_ID, "title": "Voice session"}
    ]
    conv_select = mocker.MagicMock()
    conv_select.eq.return_value.order.return_value.execute.return_value.data = [
        {"id": TEST_CONV_ID, "user_id": TEST_USER_ID, "title": "Voice session"}
    ]
    conv_select.eq.return_value.limit.return_value.execute.return_value.data = [
        {"id": TEST_CONV_ID, "user_id": TEST_USER_ID}
    ]
    conv_table.select.return_value = conv_select
    conv_table.delete.return_value.eq.return_value.execute.return_value.data = []

    # messages table
    msg_table = mocker.MagicMock()
    msg_table.insert.return_value.execute.return_value.data = [
        {"id": "msg-1", "role": "user", "content": "hello"}
    ]
    msg_select = mocker.MagicMock()
    msg_select.eq.return_value.order.return_value.execute.return_value.data = [
        {"role": "user", "content": "hello", "created_at": "2024-01-01T00:00:00"}
    ]
    msg_table.select.return_value = msg_select

    # user_memory table
    mem_table = mocker.MagicMock()
    mem_select = mocker.MagicMock()
    mem_select.eq.return_value.limit.return_value.execute.return_value.data = []
    mem_table.select.return_value = mem_select
    mem_table.upsert.return_value.execute.return_value.data = [{}]

    # documents table
    doc_table = mocker.MagicMock()
    doc_table.insert.return_value.execute.return_value.data = [{"id": "doc-1"}]
    doc_table.delete.return_value.eq.return_value.execute.return_value.data = []

    def table_router(name):
        return {
            "conversations": conv_table,
            "messages": msg_table,
            "user_memory": mem_table,
            "documents": doc_table,
        }.get(name, mocker.MagicMock())

    mock_db.table.side_effect = table_router

    # storage
    mock_db.storage.from_.return_value.list.return_value = []
    mock_db.storage.from_.return_value.download.return_value = b"# Title\nSome content."

    # rpc (vector search)
    mock_db.rpc.return_value.execute.return_value.data = []

    return mock_db


@pytest.fixture
def mock_supabase(mocker):
    mock_db = _make_mock_db(mocker)
    mocker.patch("app.database.get_supabase", return_value=mock_db)
    mocker.patch("app.conversation_repo.get_supabase", return_value=mock_db)
    mocker.patch("app.message_repo.get_supabase", return_value=mock_db)
    mocker.patch("app.memory_repo.get_supabase", return_value=mock_db)
    mocker.patch("app.document_repo.get_supabase", return_value=mock_db)
    mocker.patch("app.doc_processor.get_supabase", return_value=mock_db)
    return mock_db
