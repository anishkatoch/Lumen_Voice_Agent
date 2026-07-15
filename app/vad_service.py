"""
VAD service — uses Silero neural VAD model directly (no torchaudio needed).
Silero distinguishes actual speech from background noise by phoneme patterns,
not energy level, so it works correctly even on quiet microphones.
"""
import asyncio
import io
import wave
import torch
from app.config import VAD_SILERO_MIN, VAD_SILERO_MAX

_model = None


def _load_model():
    global _model
    if _model is None:
        # Load only the model — skip utils which import torchaudio
        _model, _ = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            force_reload=False,
            onnx=False,
            trust_repo=True,
        )
    return _model


def _detect_speech_sync(audio_bytes: bytes, sensitivity: float = 0.5) -> bool:
    """
    Silero VAD on raw PCM bytes (int16, mono, 16kHz).
    Calls the model directly — no torchaudio dependency.
    sensitivity 0.0-1.0: higher = easier to trigger.
    Falls back to RMS if model call fails.
    """
    if len(audio_bytes) % 2 != 0:
        audio_bytes = audio_bytes[:-1]
    if not audio_bytes:
        return False

    audio_array = torch.frombuffer(bytearray(audio_bytes), dtype=torch.int16).float() / 32768.0

    try:
        model = _load_model()

        # sensitivity → Silero confidence threshold (inverse)
        # sensitivity=1.0 → threshold=0.20 (very easy)
        # sensitivity=0.7 → threshold=0.38 (balanced)
        # sensitivity=0.5 → threshold=0.50 (normal)
        # sensitivity=0.0 → threshold=0.80 (very strict)
        silero_threshold = VAD_SILERO_MIN + (1.0 - sensitivity) * (VAD_SILERO_MAX - VAD_SILERO_MIN)

        # Silero requires exactly 512 samples per call at 16kHz (32ms windows).
        # Twilio media events are only 20ms (320 samples after upsampling) —
        # shorter than one window — so pad up to WINDOW instead of silently
        # skipping the model call (which would always yield prob=0.0).
        WINDOW = 512
        if len(audio_array) < WINDOW:
            audio_array = torch.nn.functional.pad(audio_array, (0, WINDOW - len(audio_array)))
        with torch.no_grad():
            model.reset_states()
            probs = []
            for i in range(0, len(audio_array) - WINDOW + 1, WINDOW):
                prob = float(model(audio_array[i:i + WINDOW], 16000))
                probs.append(prob)

        max_prob = max(probs) if probs else 0.0
        is_speech = max_prob > silero_threshold
        print(f"[VAD] silero max_prob={max_prob:.3f} threshold={silero_threshold:.2f} speech={is_speech}")
        return is_speech

    except Exception as e:
        # Fallback: RMS energy detection
        print(f"[VAD] Silero error ({e}), using RMS fallback")
        rms = float(audio_array.pow(2).mean().sqrt())
        rms_threshold = 0.005 + (1.0 - sensitivity) * 0.095
        is_speech = rms > rms_threshold
        print(f"[VAD] rms={rms:.4f} threshold={rms_threshold:.4f} speech={is_speech}")
        return is_speech


async def detect_speech(audio_bytes: bytes, sensitivity: float = 0.5) -> bool:
    """Async wrapper — runs in threadpool so it never blocks the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _detect_speech_sync, audio_bytes, sensitivity)


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
