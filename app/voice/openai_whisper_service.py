"""
OpenAI Whisper STT - drop-in for deepgram_service when stt_provider = "openai_whisper".
StreamingWhisperTranscriber buffers audio and transcribes at finish() time (Whisper is batch-only).
"""
import io
import wave
from openai import AsyncOpenAI, AsyncAzureOpenAI
from app.core.config import (
    OPENAI_API_KEY,
    AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION,
    AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT,
)

_openai = AsyncOpenAI(api_key=OPENAI_API_KEY)
_azure_openai = (
    AsyncAzureOpenAI(
        api_key=AZURE_OPENAI_API_KEY,
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
    )
    if AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT
    else None
)


_MIN_AUDIO_SECONDS = 0.15  # Whisper's API requires >= 0.1s - stay safely above that


async def _transcribe(file_tuple: tuple) -> str | None:
    """Azure OpenAI (gpt-4o-transcribe) first, plain OpenAI Whisper as the
    fallback only if Azure is unconfigured or the call fails - same
    Azure-first pattern as app/voice/llm_service.py and app/core/embeddings.py."""
    if _azure_openai is not None:
        try:
            from app.admin.admin_config_service import get as get_config
            deployment = get_config("azure_openai_transcribe_deployment") or AZURE_OPENAI_TRANSCRIBE_DEPLOYMENT
            resp = await _azure_openai.audio.transcriptions.create(
                model=deployment, file=file_tuple, language="en",
            )
            text = resp.text.strip()
            return text if text else None
        except Exception as e:
            print(f"[Whisper] Azure transcribe error: {e} - falling back to plain OpenAI")
    try:
        resp = await _openai.audio.transcriptions.create(
            model="whisper-1", file=file_tuple, language="en",
        )
        text = resp.text.strip()
        return text if text else None
    except Exception as e:
        print(f"[Whisper] Error: {e}")
        return None


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


class StreamingWhisperTranscriber:
    """Same interface as deepgram_service.StreamingTranscriber but uses Whisper batch API."""

    def __init__(self):
        self._chunks: list[bytes] = []

    async def start(self):
        self._chunks = []

    async def send(self, audio_bytes: bytes):
        self._chunks.append(audio_bytes)

    async def finish(self) -> str | None:
        if not self._chunks:
            return None
        pcm = b"".join(self._chunks)
        wav = _pcm_to_wav(pcm)
        if _wav_duration_seconds(wav) < _MIN_AUDIO_SECONDS:
            # A VAD blip with barely any real audio - Whisper's API rejects
            # anything under 0.1s with a 400 (audio_too_short); there's
            # nothing worth transcribing here anyway.
            return None
        return await _transcribe(("audio.wav", wav, "audio/wav"))

    def get_last_partial(self) -> str | None:
        """Whisper is batch-only - no partial transcript exists before finish()."""
        return None


async def transcribe_wav_whisper(wav_bytes: bytes) -> str | None:
    """Batch-transcribe a WAV file - Azure OpenAI (gpt-4o-transcribe) first,
    plain OpenAI Whisper as fallback (see _transcribe)."""
    if _wav_duration_seconds(wav_bytes) < _MIN_AUDIO_SECONDS:
        # Same guard as StreamingWhisperTranscriber.finish() - avoids a
        # guaranteed 400 audio_too_short for callers that pass raw/short
        # buffers directly (e.g. a Deepgram-empty-transcript fallback).
        return None
    return await _transcribe(("audio.wav", wav_bytes, "audio/wav"))


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
