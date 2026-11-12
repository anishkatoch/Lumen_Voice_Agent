import asyncio
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from app.core.config import (
    ELEVENLABS_API_KEY, TTS_VOICE_ID, TTS_MODEL_ID, TTS_OUTPUT_FORMAT,
    TTS_STABILITY, TTS_SIMILARITY_BOOST, TTS_STYLE, TTS_SPEAKER_BOOST,
)


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
    try:
        # Client construction is inside the try too - an auth/network error
        # here must also trigger the OpenAI fallback below, not crash.
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        settings = VoiceSettings(
            stability=TTS_STABILITY,
            similarity_boost=TTS_SIMILARITY_BOOST,
            style=TTS_STYLE,
            use_speaker_boost=TTS_SPEAKER_BOOST,
        )
        voice_id = get_config("tts_elevenlabs_voice_id") or TTS_VOICE_ID
        audio_stream = await asyncio.to_thread(
            client.text_to_speech.convert_as_stream,
            voice_id=voice_id,
            text=text,
            model_id=get_config("tts_elevenlabs_model_id") or TTS_MODEL_ID,
            voice_settings=settings,
            output_format=TTS_OUTPUT_FORMAT,
        )
        for chunk in audio_stream:
            if chunk:
                chunk_count += 1
                yield chunk
        print(f"[ElevenLabs] Done - sent {chunk_count} chunks")
    except Exception as e:
        import traceback
        print(f"[ElevenLabs] TTS error: {type(e).__name__}: {e}")
        traceback.print_exc()

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
