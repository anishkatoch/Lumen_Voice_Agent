"""OpenAI Realtime API bridge - the third voice pipeline.

Unlike the cascaded Deepgram (STT) -> llm_service (LLM) -> ElevenLabs/OpenAI
(TTS) pipelines, this one is a single persistent speech-to-speech session:
raw caller audio is forwarded straight to OpenAI's Realtime model and the
model's own audio comes straight back, with OpenAI doing turn detection,
transcription and response generation server-side. There is no local VAD,
no Deepgram, no separate TTS call - none of that runs when this pipeline is
active.

Always uses the plain OPENAI_API_KEY (never Azure) - model id defaults to
REALTIME_MODEL in config.yaml but is overridable via the admin_config table's
"realtime_model" key (Providers tab), read in RealtimeSession.__init__ when
no explicit model is passed in; whether this pipeline is active and which
voice it uses are also live admin-panel settings that callers read
themselves and pass into RealtimeSession.
"""
import asyncio
import base64
import json
from typing import AsyncGenerator

import websockets

from app.core.config import OPENAI_API_KEY, REALTIME_MODEL

DEFAULT_VOICE = "marin"

REALTIME_WS_URL = "wss://api.openai.com/v1/realtime"

# Bytes of raw audio per millisecond, used to tell OpenAI how much of an
# interrupted response the caller actually heard (conversation.item.truncate).
BYTES_PER_MS = {
    "g711_ulaw": 8.0,     # 8 kHz, 8-bit mono mu-law - telephony (Plivo/Twilio), no resampling needed
    "pcm16": 48.0,        # Realtime's pcm16 is always 24 kHz, 16-bit mono
}


class RealtimeSession:
    """One call/session's connection to the OpenAI Realtime API."""

    def __init__(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        audio_format: str = "g711_ulaw",
        voice: str | None = None,
        model: str | None = None,
    ):
        self.instructions = instructions
        self.tools = tools or []
        self.audio_format = audio_format
        self.voice = voice or DEFAULT_VOICE
        if model:
            self.model = model
        else:
            from app.admin.admin_config_service import get as get_config
            self.model = get_config("realtime_model") or REALTIME_MODEL
        self.bytes_per_ms = BYTES_PER_MS.get(audio_format, 48.0)
        self._ws = None

    async def connect(self):
        url = f"{REALTIME_WS_URL}?model={self.model}"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(url, additional_headers=headers, max_size=None)
        await self._send({
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": self.instructions,
                "voice": self.voice,
                "input_audio_format": self.audio_format,
                "output_audio_format": self.audio_format,
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {"type": "server_vad"},
                "tools": self.tools,
                "tool_choice": "auto" if self.tools else "none",
            },
        })

    async def _send(self, event: dict):
        await self._ws.send(json.dumps(event))

    async def append_audio(self, raw_bytes: bytes):
        await self.append_audio_b64(base64.b64encode(raw_bytes).decode())

    async def append_audio_b64(self, b64_payload: str):
        """Like append_audio, but takes an already-base64 payload - telephony
        callers (Plivo/Twilio) receive their inbound audio base64-encoded and
        in the exact g711_ulaw format this session negotiates, so it can be
        forwarded verbatim with no decode/re-encode round trip."""
        await self._send({"type": "input_audio_buffer.append", "audio": b64_payload})

    async def say(self, text: str):
        """Force the model's very next turn to be exactly this text, spoken
        verbatim - used for scripted greetings (HR interview/campaign calls)
        where the caller must hear the exact configured line, not the
        model's paraphrase of it."""
        await self._send({
            "type": "response.create",
            "response": {"instructions": f'Say exactly the following and nothing else: "{text}"'},
        })

    async def create_response(self):
        await self._send({"type": "response.create"})

    async def cancel_response(self):
        await self._send({"type": "response.cancel"})

    async def truncate(self, item_id: str, audio_end_ms: int):
        await self._send({
            "type": "conversation.item.truncate",
            "item_id": item_id,
            "content_index": 0,
            "audio_end_ms": max(audio_end_ms, 0),
        })

    async def send_tool_result(self, call_id: str, output: str):
        await self._send({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id, "output": output},
        })
        await self.create_response()

    async def events(self) -> AsyncGenerator[dict, None]:
        async for raw in self._ws:
            yield json.loads(raw)

    async def close(self):
        try:
            await self._ws.close()
        except Exception:
            pass


def build_realtime_tool(fn: dict) -> dict:
    """Convert a saved agent function dict into a Realtime tool definition -
    same shape as llm_service.build_openai_tool but flat (no nested
    "function" key), which is what the Realtime API expects."""
    properties: dict = {}
    required: list[str] = []
    for p in fn.get("parameters") or []:
        ptype = p.get("type", "string")
        if ptype not in ("string", "number", "integer", "boolean", "array", "object"):
            ptype = "string"
        properties[p["name"]] = {"type": ptype, "description": p.get("description", "")}
        required.append(p["name"])
    return {
        "type": "function",
        "name": fn["name"],
        "description": fn.get("description") or f"Call {fn['name']}",
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


def resample_pcm16(pcm_bytes: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample int16 mono PCM via linear interpolation - used only for the
    browser pipeline, where the client's mic/playback rate (commonly 16 kHz)
    differs from the Realtime API's fixed 24 kHz pcm16. Telephony (Plivo/
    Twilio) never calls this - their g711_ulaw audio passes straight through."""
    if from_rate == to_rate or not pcm_bytes:
        return pcm_bytes
    import numpy as np
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
    n_out = int(len(samples) * to_rate / from_rate)
    if n_out <= 0:
        return b""
    indices = np.linspace(0, len(samples) - 1, n_out)
    resampled = np.interp(indices, np.arange(len(samples)), samples).astype(np.int16)
    return resampled.tobytes()
