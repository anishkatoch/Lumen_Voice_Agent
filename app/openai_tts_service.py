"""
OpenAI TTS — drop-in for elevenlabs_service when tts_provider = "openai_tts".
OpenAI TTS outputs PCM at 24 kHz; we resample to 16 kHz to match the frontend.
"""
from typing import AsyncGenerator
import numpy as np
from openai import AsyncOpenAI
from app.config import OPENAI_API_KEY


async def text_to_speech_openai(
    text: str,
    voice: str = "alloy",
    model: str = "tts-1",
) -> AsyncGenerator[bytes, None]:
    """Yields raw PCM chunks (16 kHz, 16-bit signed, mono)."""
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    print(f"[OpenAI TTS] voice={voice} model={model} text={text[:60]}")
    try:
        # Collect the full PCM-24kHz response then resample
        raw_chunks: list[bytes] = []
        async with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=text,
            response_format="pcm",  # raw 16-bit signed PCM at 24 kHz
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=8192):
                if chunk:
                    raw_chunks.append(chunk)

        pcm_24k = b"".join(raw_chunks)
        pcm_16k = _resample(pcm_24k, from_rate=24000, to_rate=16000)

        CHUNK = 8192
        count = 0
        for i in range(0, len(pcm_16k), CHUNK):
            yield pcm_16k[i:i + CHUNK]
            count += 1
        print(f"[OpenAI TTS] Done — {count} chunks")
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
