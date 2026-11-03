"""
OpenAI Whisper STT - drop-in for deepgram_service when stt_provider = "openai_whisper".
StreamingWhisperTranscriber buffers audio and transcribes at finish() time (Whisper is batch-only).
"""
import io
import wave
from openai import AsyncOpenAI
from app.core.config import OPENAI_API_KEY


_MIN_AUDIO_SECONDS = 0.15  # Whisper's API requires >= 0.1s - stay safely above that


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


class StreamingWhisperTranscriber:
    """Same interface as deepgram_service.StreamingTranscriber but uses Whisper batch API."""

    def __init__(self):
        self._client = AsyncOpenAI(api_key=OPENAI_API_KEY)
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
        try:
            resp = await self._client.audio.transcriptions.create(
                model="whisper-1",
                file=("audio.wav", wav, "audio/wav"),
                language="en",
            )
            text = resp.text.strip()
            return text if text else None
        except Exception as e:
            print(f"[Whisper] Transcription error: {e}")
            return None

    def get_last_partial(self) -> str | None:
        """Whisper is batch-only - no partial transcript exists before finish()."""
        return None


async def transcribe_wav_whisper(wav_bytes: bytes) -> str | None:
    """Batch-transcribe a WAV file via Whisper."""
    if _wav_duration_seconds(wav_bytes) < _MIN_AUDIO_SECONDS:
        # Same guard as StreamingWhisperTranscriber.finish() - avoids a
        # guaranteed 400 audio_too_short for callers that pass raw/short
        # buffers directly (e.g. a Deepgram-empty-transcript fallback).
        return None
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    try:
        resp = await client.audio.transcriptions.create(
            model="whisper-1",
            file=("audio.wav", wav_bytes, "audio/wav"),
            language="en",
        )
        text = resp.text.strip()
        return text if text else None
    except Exception as e:
        print(f"[Whisper] Error: {e}")
        return None


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()
