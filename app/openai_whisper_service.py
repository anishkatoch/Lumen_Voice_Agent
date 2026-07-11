"""
OpenAI Whisper STT — drop-in for deepgram_service when stt_provider = "openai_whisper".
StreamingWhisperTranscriber buffers audio and transcribes at finish() time (Whisper is batch-only).
"""
import io
import wave
from openai import AsyncOpenAI
from app.config import OPENAI_API_KEY


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


async def transcribe_wav_whisper(wav_bytes: bytes) -> str | None:
    """Batch-transcribe a WAV file via Whisper."""
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
