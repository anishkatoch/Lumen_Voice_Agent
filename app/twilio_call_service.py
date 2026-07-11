"""
Audio conversion utilities for Twilio Media Streams.

Twilio sends/receives G.711 μ-law at 8 kHz, mono.
Our pipeline uses PCM int16 at 16 kHz, mono.

audioop was removed in Python 3.13, so we use numpy.
"""

import base64
import numpy as np


# ─── G.711 μ-law lookup tables ────────────────────────────────────────────────

_DECODE_TABLE: np.ndarray | None = None
_ENCODE_TABLE: np.ndarray | None = None


def _build_decode_table() -> np.ndarray:
    """Build the standard ITU-T G.711 μ-law → linear decode table (256 entries)."""
    idx = np.arange(256, dtype=np.uint8)
    inverted = (idx ^ 0xFF).astype(np.int16)
    sign = inverted >> 7
    exp = (inverted >> 4) & 0x07
    mantissa = inverted & 0x0F
    magnitude = ((mantissa + 0x21) << (exp + 2)).astype(np.int32)
    sample = np.where(sign, -magnitude, magnitude).astype(np.int16)
    return sample


def _build_encode_table() -> np.ndarray:
    """Build a linear → μ-law encode table (65536 entries for uint16 index)."""
    mu = 255.0
    # We'll handle sign separately; build for magnitude 0..32767
    mag = np.arange(32768, dtype=np.float32) / 32767.0
    compressed = (np.log1p(mu * mag) / np.log1p(mu) * 127 + 0.5).astype(np.uint8)
    return compressed


def _get_tables():
    global _DECODE_TABLE, _ENCODE_TABLE
    if _DECODE_TABLE is None:
        _DECODE_TABLE = _build_decode_table()
        _ENCODE_TABLE = _build_encode_table()
    return _DECODE_TABLE, _ENCODE_TABLE


# ─── Core conversion ──────────────────────────────────────────────────────────

def mulaw_decode(mulaw_bytes: bytes) -> bytes:
    """G.711 μ-law bytes → int16 PCM bytes (same sample rate, 8 kHz)."""
    decode_table, _ = _get_tables()
    idx = np.frombuffer(mulaw_bytes, dtype=np.uint8)
    return decode_table[idx].tobytes()


def mulaw_encode(pcm_bytes: bytes) -> bytes:
    """int16 PCM bytes (8 kHz) → G.711 μ-law bytes."""
    _, encode_table = _get_tables()
    samples = np.frombuffer(pcm_bytes, dtype=np.int16)
    sign = (samples < 0).astype(np.uint8)
    magnitude = np.abs(samples.astype(np.int32)).clip(0, 32767).astype(np.uint16)
    quantized = encode_table[magnitude]
    # Pack sign into MSB, then invert all bits per G.711 spec
    return ((quantized | (sign << 7)) ^ 0xFF).tobytes()


def resample_8k_to_16k(pcm8k: bytes) -> bytes:
    """Upsample int16 PCM from 8 kHz to 16 kHz via linear interpolation."""
    samples = np.frombuffer(pcm8k, dtype=np.int16).astype(np.float32)
    n = len(samples)
    if n == 0:
        return b""
    # Interpolate between each pair of samples
    out = np.empty(n * 2, dtype=np.float32)
    out[0::2] = samples
    out[1::2] = np.append((samples[:-1] + samples[1:]) / 2.0, samples[-1])
    return out.astype(np.int16).tobytes()


def resample_16k_to_8k(pcm16k: bytes) -> bytes:
    """Downsample int16 PCM from 16 kHz to 8 kHz by decimation."""
    samples = np.frombuffer(pcm16k, dtype=np.int16)
    return samples[::2].copy().tobytes()


# ─── High-level helpers ───────────────────────────────────────────────────────

def twilio_payload_to_pcm16k(payload_b64: str) -> bytes:
    """Twilio base64 μ-law 8 kHz payload → PCM int16 16 kHz bytes."""
    mulaw = base64.b64decode(payload_b64)
    pcm8k = mulaw_decode(mulaw)
    return resample_8k_to_16k(pcm8k)


def pcm16k_to_twilio_payload(pcm16k: bytes) -> str:
    """PCM int16 16 kHz bytes → Twilio base64 μ-law 8 kHz payload."""
    pcm8k = resample_16k_to_8k(pcm16k)
    mulaw = mulaw_encode(pcm8k)
    return base64.b64encode(mulaw).decode()


async def text_to_speech_twilio(text: str):
    """
    Yields raw μ-law 8 kHz bytes ready to send to Twilio Media Streams.

    ElevenLabs path: requests ulaw_8000 format directly — no conversion needed.
    OpenAI TTS path: gets PCM 16 kHz then downsamples + encodes to μ-law 8 kHz.

    Do NOT use text_to_speech_stream() here — it returns MP3 or PCM for the browser,
    not the μ-law format Twilio requires.
    """
    import asyncio
    from app.admin_config_service import get as get_config

    provider = get_config("tts_provider")

    if provider == "openai_tts":
        from app.openai_tts_service import text_to_speech_openai
        voice = get_config("tts_openai_voice") or "alloy"
        model = get_config("tts_openai_model") or "tts-1"
        async for pcm16k_chunk in text_to_speech_openai(text, voice=voice, model=model):
            if pcm16k_chunk:
                pcm8k = resample_16k_to_8k(pcm16k_chunk)
                yield mulaw_encode(pcm8k)
    else:
        # ElevenLabs — ulaw_8000 is native μ-law 8 kHz, exactly what Twilio wants
        from elevenlabs.client import ElevenLabs
        from elevenlabs import VoiceSettings
        from app.config import (
            ELEVENLABS_API_KEY, TTS_VOICE_ID, TTS_MODEL_ID,
            TTS_STABILITY, TTS_SIMILARITY_BOOST, TTS_STYLE, TTS_SPEAKER_BOOST,
        )
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        settings = VoiceSettings(
            stability=TTS_STABILITY,
            similarity_boost=TTS_SIMILARITY_BOOST,
            style=TTS_STYLE,
            use_speaker_boost=TTS_SPEAKER_BOOST,
        )
        print(f"[Twilio TTS] Requesting ulaw_8000 for: {text[:60]}")
        queue: asyncio.Queue = asyncio.Queue()

        def _stream_to_queue():
            """Run ElevenLabs sync iterator in a thread, push chunks to the async queue."""
            try:
                audio_stream = client.text_to_speech.convert_as_stream(
                    voice_id=TTS_VOICE_ID,
                    text=text,
                    model_id=TTS_MODEL_ID,
                    voice_settings=settings,
                    output_format="ulaw_8000",
                )
                for chunk in audio_stream:
                    if chunk:
                        queue.put_nowait(chunk)
            except Exception as e:
                import traceback
                print(f"[Twilio TTS] Error: {type(e).__name__}: {e}")
                traceback.print_exc()
            finally:
                queue.put_nowait(None)  # sentinel

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, _stream_to_queue)
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
