"""
Simulates a real Twilio Media Stream call against a LOCAL server, so the full
pipeline (VAD, barge-in, Deepgram, LLM, TTS) can be tested without a real
phone call or a Hugging Face redeploy.

It speaks the "caller" side using the app's own ElevenLabs TTS (real, actually
transcribable speech), streams it to /twilio/stream in real 20ms chunks exactly
like Twilio does, and saves the agent's spoken replies to a WAV file so you can
listen to them.

Prerequisites:
    1. Start the server in another terminal:
         uv run uvicorn app.main:app --host 0.0.0.0 --port 8004
    2. .env must have valid OPENAI_API_KEY / DEEPGRAM_API_KEY / ELEVENLABS_API_KEY
       / DATABASE_URL etc. (same ones the real server uses)

Usage:
    python scripts/simulate_twilio_call.py
    python scripts/simulate_twilio_call.py --say "Can you help me reset my password?"
    python scripts/simulate_twilio_call.py --barge-in
    python scripts/simulate_twilio_call.py --to "+17627618325" --host localhost:8004
"""
import argparse
import asyncio
import base64
import json
import sys
import time
import wave
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.twilio_call_service import mulaw_decode, text_to_speech_twilio  # noqa: E402

CHUNK_BYTES = 160  # 20ms of mu-law @ 8kHz, matches a real Twilio media event
CHUNK_SECONDS = 0.02


async def _synthesize_caller_audio(text: str) -> bytes:
    """Reuse the app's own TTS to produce real, transcribable mu-law speech."""
    chunks = [c async for c in text_to_speech_twilio(text)]
    return b"".join(chunks)


SILENCE_TAIL_CHUNKS = 35  # ~700ms - Twilio streams continuously, incl. silence between
                          # utterances; without this the app's SILENCE_THRESHOLD (25
                          # chunks) never gets reached and the transcript never finalizes
_MULAW_SILENCE_BYTE = 0xFF


async def _stream_caller_audio(ws, mulaw_bytes: bytes, stream_sid: str, label: str, trailing_silence: bool = True):
    print(f"[sim] streaming {len(mulaw_bytes)} bytes as caller ({label})...")
    payload_bytes = mulaw_bytes
    if trailing_silence:
        payload_bytes += bytes([_MULAW_SILENCE_BYTE]) * (CHUNK_BYTES * SILENCE_TAIL_CHUNKS)
    for i in range(0, len(payload_bytes), CHUNK_BYTES):
        chunk = payload_bytes[i:i + CHUNK_BYTES]
        payload = base64.b64encode(chunk).decode()
        await ws.send(json.dumps({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": payload},
        }))
        await asyncio.sleep(CHUNK_SECONDS)
    print(f"[sim] done streaming ({label})")


async def _receiver(ws, received_mulaw: bytearray, stop_event: asyncio.Event, first_media_event: asyncio.Event):
    try:
        async for raw in ws:
            data = json.loads(raw)
            event = data.get("event")
            if event == "media":
                if not first_media_event.is_set():
                    first_media_event.set()
                    print("[sim] <- agent started responding")
                payload = data["media"]["payload"]
                received_mulaw.extend(base64.b64decode(payload))
            elif event == "clear":
                print("[sim] <- received 'clear' (barge-in flush confirmed)")
            else:
                print(f"[sim] <- {event}")
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        stop_event.set()


def _save_wav(mulaw_bytes: bytes, path: str):
    if not mulaw_bytes:
        print("[sim] no agent audio received - nothing to save")
        return
    pcm = mulaw_decode(mulaw_bytes)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(pcm)
    print(f"[sim] saved agent's response audio -> {path} ({len(mulaw_bytes)/8000:.1f}s)")


async def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost:8004", help="host:port of the local server")
    parser.add_argument("--to", default="+17627618325", help="phone number to simulate calling (must exist + be active in twilio_phone_numbers)")
    parser.add_argument("--campaign-candidate-id", default="", help="if set, simulates a call-campaign call instead of inbound (sends campaign_candidate_id instead of to)")
    parser.add_argument("--say", default="Hi, can you help me with something?", help="what the simulated caller says")
    parser.add_argument("--barge-in", action="store_true", help="interrupt the agent shortly after it starts speaking")
    parser.add_argument("--interrupt-text", default="Wait, actually, never mind that.", help="what to say when interrupting")
    parser.add_argument("--out", default="/tmp/simulated_call_response.wav", help="where to save the agent's spoken reply")
    args = parser.parse_args()

    uri = f"ws://{args.host}/twilio/stream"
    print(f"[sim] connecting to {uri}")

    caller_audio = await _synthesize_caller_audio(args.say)
    interrupt_audio = await _synthesize_caller_audio(args.interrupt_text) if args.barge_in else b""

    received_mulaw = bytearray()
    stop_event = asyncio.Event()
    first_media_event = asyncio.Event()

    async with websockets.connect(uri) as ws:
        stream_sid = "MZsimulated0000000000000000000000"
        custom_params = (
            {"campaign_candidate_id": args.campaign_candidate_id}
            if args.campaign_candidate_id else {"to": args.to}
        )
        await ws.send(json.dumps({
            "event": "start",
            "start": {
                "streamSid": stream_sid,
                "customParameters": custom_params,
            },
        }))
        print(f"[sim] sent start event (customParameters={custom_params})")

        recv_task = asyncio.create_task(_receiver(ws, received_mulaw, stop_event, first_media_event))

        # Let the agent's greeting play out first, same as a real inbound call
        await asyncio.sleep(3)

        await _stream_caller_audio(ws, caller_audio, stream_sid, "initial utterance")

        if args.barge_in:
            # first_media_event fires on the GREETING, not the actual reply to our
            # question - that already happened before we even spoke. Instead, wait
            # for a NEW burst of audio (byte count growing beyond what's already
            # buffered) to detect when the agent's real response starts, allowing
            # time for Deepgram finalization + LLM latency.
            print("[sim] waiting for agent's real response to start, to test barge-in...")
            baseline_len = len(received_mulaw)
            waited = 0.0
            while len(received_mulaw) <= baseline_len and waited < 20:
                await asyncio.sleep(0.1)
                waited += 0.1
            if len(received_mulaw) <= baseline_len:
                print("[sim] WARNING: agent never started a new response within 20s")
            else:
                print(f"[sim] agent's real response started after {waited:.1f}s")
            await asyncio.sleep(0.3)  # let a little of the response play first
            await _stream_caller_audio(ws, interrupt_audio, stream_sid, "BARGE-IN interruption")

        # Wait for the agent to finish responding
        await asyncio.sleep(15)

        await ws.send(json.dumps({"event": "stop", "streamSid": stream_sid}))
        await asyncio.sleep(0.5)
        recv_task.cancel()

    _save_wav(bytes(received_mulaw), args.out)


if __name__ == "__main__":
    asyncio.run(main())
