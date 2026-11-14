"""Azure AI Speech (Cognitive Services) neural TTS.

A different Azure resource from Azure OpenAI (own subscription key + region,
REST API instead of an OpenAI-compatible endpoint). Used as the primary TTS
source for the "openai_classic" voice pipeline - text_to_speech_openai_classic
below tries this first and falls back to plain OpenAI TTS only if Azure
Speech is unconfigured or the call fails.
"""
import httpx
from typing import AsyncGenerator

from app.core.config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION, AZURE_SPEECH_VOICE

_SSML_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}

# Shared client: connection pooling across sentences. A new AsyncClient per
# call paid a TCP+TLS handshake for every spoken sentence.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=20.0)
    return _http_client


def _escape_ssml(text: str) -> str:
    for ch, esc in _SSML_ESCAPES.items():
        text = text.replace(ch, esc)
    return text


async def text_to_speech_azure(text: str, voice: str | None = None) -> AsyncGenerator[bytes, None]:
    """Yields raw PCM chunks (16 kHz, 16-bit signed, mono) - same output
    shape as openai_tts_service.text_to_speech_openai, so callers can swap
    between them without touching any downstream resampling/encoding."""
    if not AZURE_SPEECH_KEY or not AZURE_SPEECH_REGION:
        raise RuntimeError("Azure Speech is not configured (AZURE_SPEECH_KEY/AZURE_SPEECH_REGION)")

    from app.admin.admin_config_service import get as get_config
    voice_name = voice or get_config("azure_speech_voice") or AZURE_SPEECH_VOICE
    ssml = (
        '<speak version="1.0" xml:lang="en-US">'
        f'<voice name="{voice_name}">{_escape_ssml(text)}</voice></speak>'
    )
    url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        # Headerless raw PCM - avoids parsing a WAV/RIFF container.
        "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm",
    }
    # Stream the body as Azure synthesizes it - collecting resp.content in
    # full before yielding added the entire synthesis time to first-audio
    # latency on every sentence spoken through this path.
    client = _get_http_client()
    async with client.stream("POST", url, headers=headers, content=ssml.encode("utf-8")) as resp:
        resp.raise_for_status()
        # Keep chunks sample-aligned (int16) across yields.
        carry = b""
        async for chunk in resp.aiter_bytes(chunk_size=4096):
            if not chunk:
                continue
            data = carry + chunk
            usable = len(data) - (len(data) % 2)
            carry = data[usable:]
            if usable:
                yield data[:usable]


async def text_to_speech_openai_classic(
    text: str, openai_voice: str = "alloy", openai_model: str = "tts-1",
) -> AsyncGenerator[bytes, None]:
    """TTS for the "openai_classic" pipeline - Azure AI Speech first, plain
    OpenAI TTS as the fallback only if Azure Speech is unconfigured or the
    call fails before any audio has been produced. Audio is streamed through
    as Azure synthesizes it; an error after audio has already been spoken
    stops the sentence rather than falling back (restarting it from another
    voice mid-sentence would sound worse than ending early)."""
    if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        yielded_any = False
        try:
            async for chunk in text_to_speech_azure(text):
                if chunk:
                    yielded_any = True
                    yield chunk
            if yielded_any:
                return
        except Exception as e:
            print(f"[Azure Speech] TTS error: {e}" + (" - falling back to OpenAI TTS" if not yielded_any else " (mid-stream, not falling back)"))
            if yielded_any:
                return

    from app.voice.openai_tts_service import text_to_speech_openai
    async for chunk in text_to_speech_openai(text, voice=openai_voice, model=openai_model):
        yield chunk
