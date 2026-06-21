"""
Silero VAD service.
Runs in a threadpool (asyncio.run_in_executor) so it never blocks the event loop.
"""
import asyncio
import io
import wave
import torch

_model = None
_utils = None


def _load_model():
    global _model, _utils
    if _model is None:
        _model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            onnx=False,
        )
    return _model, _utils


def _detect_speech_sync(audio_bytes: bytes, sensitivity: float = 0.2) -> bool:
    """
    RMS energy-based VAD on raw PCM bytes (int16, mono).
    sensitivity maps 0.0-1.0 to a RMS threshold (lower = more sensitive).
    """
    if len(audio_bytes) % 2 != 0:
        audio_bytes = audio_bytes[:-1]
    if not audio_bytes:
        return False

    audio_array = torch.frombuffer(bytearray(audio_bytes), dtype=torch.int16).float() / 32768.0
    rms = float(audio_array.pow(2).mean().sqrt())

    # Map sensitivity (0.0-1.0) → RMS threshold (0.005-0.1)
    # Lower sensitivity value = easier to trigger
    rms_threshold = 0.005 + sensitivity * 0.095
    is_speech = rms > rms_threshold
    print(f"[VAD] rms={rms:.4f} threshold={rms_threshold:.4f} speech={is_speech}")
    return is_speech


async def detect_speech(audio_bytes: bytes, sensitivity: float = 0.2) -> bool:
    """Async wrapper — runs VAD in threadpool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, _detect_speech_sync, audio_bytes, sensitivity
    )


def build_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wrap raw PCM bytes in a WAV container for Deepgram."""
    if len(pcm_bytes) % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()
