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

    voice_name = voice or AZURE_SPEECH_VOICE
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
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, content=ssml.encode("utf-8"))
        resp.raise_for_status()
        audio = resp.content

    CHUNK = 8192
    for i in range(0, len(audio), CHUNK):
        yield audio[i:i + CHUNK]


async def text_to_speech_openai_classic(
    text: str, openai_voice: str = "alloy", openai_model: str = "tts-1",
) -> AsyncGenerator[bytes, None]:
    """TTS for the "openai_classic" pipeline - Azure AI Speech first, plain
    OpenAI TTS as the fallback only if Azure Speech is unconfigured or the
    call fails (collected in full before falling back, so a mid-stream
    Azure error never produces a half-spoken sentence)."""
    if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        try:
            chunks = [chunk async for chunk in text_to_speech_azure(text)]
            if chunks:
                for chunk in chunks:
                    yield chunk
                return
        except Exception as e:
            print(f"[Azure Speech] TTS error: {e} - falling back to OpenAI TTS")

    from app.voice.openai_tts_service import text_to_speech_openai
    async for chunk in text_to_speech_openai(text, voice=openai_voice, model=openai_model):
        yield chunk
