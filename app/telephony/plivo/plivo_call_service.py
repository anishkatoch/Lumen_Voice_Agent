"""
Audio helpers for Plivo Audio Streaming.

Plivo's bidirectional <Stream> speaks the same wire format we already use
for Twilio - base64 G.711 μ-law at 8 kHz, mono - so this module reuses the
mulaw/resample math from app/twilio_call_service.py instead of duplicating
it, and only adds the Plivo-specific text_to_speech wrapper.
"""

from app.telephony.twilio.twilio_call_service import (
    mulaw_decode,
    mulaw_encode,
    resample_8k_to_16k,
    resample_16k_to_8k,
    text_to_speech_twilio,
)


def plivo_payload_to_pcm16k(payload_b64: str) -> bytes:
    """Plivo base64 μ-law 8 kHz payload → PCM int16 16 kHz bytes."""
    import base64
    mulaw = base64.b64decode(payload_b64)
    pcm8k = mulaw_decode(mulaw)
    return resample_8k_to_16k(pcm8k)


def plivo_l16_payload_to_pcm16k(payload_b64: str) -> bytes:
    """Fallback path for when Plivo's "start" event reports it negotiated
    linear PCM (audio/x-l16) instead of μ-law for this call - the bytes are
    already int16 PCM at 8 kHz, so only the resample step is needed."""
    import base64
    pcm8k = base64.b64decode(payload_b64)
    return resample_8k_to_16k(pcm8k)


def pcm16k_to_plivo_payload(pcm16k: bytes) -> str:
    """PCM int16 16 kHz bytes → Plivo base64 μ-law 8 kHz payload."""
    import base64
    pcm8k = resample_16k_to_8k(pcm16k)
    mulaw = mulaw_encode(pcm8k)
    return base64.b64encode(mulaw).decode()


async def text_to_speech_plivo(text: str):
    """Yields raw μ-law 8 kHz bytes ready to send to Plivo Audio Streaming.
    Identical audio path to Twilio's - delegates straight to it."""
    async for chunk in text_to_speech_twilio(text):
        yield chunk
