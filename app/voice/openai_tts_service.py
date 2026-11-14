"""
OpenAI TTS - drop-in for elevenlabs_service when tts_provider = "openai_tts".
OpenAI TTS outputs PCM at 24 kHz; we resample to 16 kHz to match the frontend.
"""
from typing import AsyncGenerator
import numpy as np
from openai import AsyncOpenAI
from app.core.config import OPENAI_API_KEY

# One cached client (connection pool) for all TTS calls - building a new
# AsyncOpenAI per call paid a TCP+TLS handshake on every spoken sentence.
_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    return _client


async def text_to_speech_openai(
    text: str,
    voice: str = "alloy",
    model: str = "tts-1",
) -> AsyncGenerator[bytes, None]:
    """Yields raw PCM chunks (16 kHz, 16-bit signed, mono).

    Streams as the audio is generated: each HTTP chunk is resampled and
    yielded immediately, so the first audible chunk goes out as soon as
    OpenAI starts responding instead of after the full response has been
    collected (which added the entire generation time to first-audio
    latency on every sentence that used this path)."""
    client = _get_client()
    print(f"[OpenAI TTS] voice={voice} model={model} text={text[:60]}")
    try:
        count = 0
        # Carry bytes between chunks so each resampled block is a whole
        # multiple of 3 input samples (24k -> 16k is a 3:2 ratio) - keeps
        # the output sample-aligned without buffering the whole response.
        pending = b""
        async with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            response_format="pcm",  # raw 16-bit signed PCM at 24 kHz
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                if not chunk:
                    continue
                pending += chunk
                usable = len(pending) - (len(pending) % 6)
                if not usable:
                    continue
                block, pending = pending[:usable], pending[usable:]
                out = _resample(block, from_rate=24000, to_rate=16000)
                if out:
                    count += 1
                    yield out
        if len(pending) >= 2:
            out = _resample(pending[: len(pending) - (len(pending) % 2)], from_rate=24000, to_rate=16000)
            if out:
                count += 1
                yield out
        print(f"[OpenAI TTS] Done - {count} chunks")
    except Exception as e:
        import traceback
        print(f"[OpenAI TTS] Error: {type(e).__name__}: {e}")
        traceback.print_exc()


def _resample(pcm_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample int16 PCM via linear interpolation."""
    if from_rate == to_rate or not pcm_bytes:
        return pcm_bytes
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    n_out = int(len(samples) * to_rate / from_rate)
    indices = np.linspace(0, len(samples) - 1, n_out)
    resampled = np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)
    return resampled.tobytes()
