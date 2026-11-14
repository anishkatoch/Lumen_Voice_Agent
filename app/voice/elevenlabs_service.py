import asyncio
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from app.core.config import (
    ELEVENLABS_API_KEY, TTS_VOICE_ID, TTS_MODEL_ID, TTS_OUTPUT_FORMAT,
    TTS_STABILITY, TTS_SIMILARITY_BOOST, TTS_STYLE, TTS_SPEAKER_BOOST,
)

# One cached ElevenLabs client for the whole process. The underlying httpx
# pool reuses connections, so sentences after the first skip the TCP+TLS
# handshake that a per-sentence client paid every single time. The client is
# thread-safe; it is used from worker threads here and in
# app/telephony/twilio/twilio_call_service.py.
_el_client: ElevenLabs | None = None


def get_elevenlabs_client() -> ElevenLabs:
    global _el_client
    if _el_client is None:
        _el_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    return _el_client


async def text_to_speech_stream(text: str):
    """Async generator that yields PCM audio chunks from the configured TTS provider.
    Falls back to OpenAI TTS if ElevenLabs fails or returns no audio."""
    from app.admin.admin_config_service import get as get_config

    if get_config("voice_pipeline") == "openai_classic":
        from app.voice.azure_speech_tts_service import text_to_speech_openai_classic
        voice = get_config("tts_openai_voice") or "alloy"
        model = get_config("tts_openai_model") or "tts-1"
        async for chunk in text_to_speech_openai_classic(text, openai_voice=voice, openai_model=model):
            yield chunk
        return

    if get_config("tts_provider") == "openai_tts":
        from app.voice.openai_tts_service import text_to_speech_openai
        voice = get_config("tts_openai_voice") or "alloy"
        model = get_config("tts_openai_model") or "tts-1"
        async for chunk in text_to_speech_openai(text, voice=voice, model=model):
            yield chunk
        return

    print(f"[ElevenLabs] Requesting TTS for: {text[:60]}")
    chunk_count = 0
    voice_id = get_config("tts_elevenlabs_voice_id") or TTS_VOICE_ID
    model_id = get_config("tts_elevenlabs_model_id") or TTS_MODEL_ID

    # convert_as_stream is a SYNC generator (httpx under the hood) - iterating
    # it on the event loop blocked every other call and session in the process
    # for the duration of each sentence's HTTP stream. Run the entire
    # iteration in a worker thread and hand chunks over through an asyncio
    # queue via call_soon_threadsafe (put_nowait from a foreign thread is not
    # thread-safe and relied on unrelated loop activity to wake the getter).
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _worker():
        try:
            # Client access inside the try - an auth/network error must
            # trigger the OpenAI fallback below, not crash.
            client = get_elevenlabs_client()
            settings = VoiceSettings(
                stability=TTS_STABILITY,
                similarity_boost=TTS_SIMILARITY_BOOST,
                style=TTS_STYLE,
                use_speaker_boost=TTS_SPEAKER_BOOST,
            )
            audio_stream = client.text_to_speech.convert_as_stream(
                voice_id=voice_id,
                text=text,
                model_id=model_id,
                voice_settings=settings,
                output_format=TTS_OUTPUT_FORMAT,
            )
            for chunk in audio_stream:
                if chunk:
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as e:
            import traceback
            print(f"[ElevenLabs] TTS error: {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel
            except RuntimeError:
                pass  # loop already closed (shutdown)

    loop.run_in_executor(None, _worker)
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        chunk_count += 1
        yield chunk
    if chunk_count:
        print(f"[ElevenLabs] Done - sent {chunk_count} chunks")

    if chunk_count == 0:
        print("[ElevenLabs] No audio produced - falling back to OpenAI TTS")
        from app.voice.openai_tts_service import text_to_speech_openai
        async for chunk in text_to_speech_openai(text, voice="alloy", model="tts-1"):
            yield chunk


async def text_to_speech_bytes(text: str) -> bytes | None:
    """Collect full audio as bytes (fallback for non-streaming use)."""
    chunks = []
    async for chunk in text_to_speech_stream(text):
        chunks.append(chunk)
    return b"".join(chunks) if chunks else None
