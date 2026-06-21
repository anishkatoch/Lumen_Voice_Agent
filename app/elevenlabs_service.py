import asyncio
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from app.config import (
    ELEVENLABS_API_KEY, TTS_VOICE_ID, TTS_MODEL_ID, TTS_OUTPUT_FORMAT,
    TTS_STABILITY, TTS_SIMILARITY_BOOST, TTS_STYLE, TTS_SPEAKER_BOOST,
)


async def text_to_speech_stream(text: str):
    """Async generator that yields audio chunks from ElevenLabs TTS."""
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

    settings = VoiceSettings(
        stability=TTS_STABILITY,
        similarity_boost=TTS_SIMILARITY_BOOST,
        style=TTS_STYLE,
        use_speaker_boost=TTS_SPEAKER_BOOST,
    )

    print(f"[ElevenLabs] Requesting TTS for: {text[:60]}")
    try:
        audio_stream = await asyncio.to_thread(
            client.text_to_speech.convert_as_stream,
            voice_id=TTS_VOICE_ID,
            text=text,
            model_id=TTS_MODEL_ID,
            voice_settings=settings,
            output_format=TTS_OUTPUT_FORMAT,
        )
        chunk_count = 0
        for chunk in audio_stream:
            if chunk:
                chunk_count += 1
                yield chunk
        print(f"[ElevenLabs] Done — sent {chunk_count} chunks")
    except Exception as e:
        import traceback
        print(f"[ElevenLabs] TTS error: {type(e).__name__}: {e}")
        traceback.print_exc()
        return


async def text_to_speech_bytes(text: str) -> bytes | None:
    """Collect full audio as bytes (fallback for non-streaming use)."""
    chunks = []
    async for chunk in text_to_speech_stream(text):
        chunks.append(chunk)
    return b"".join(chunks) if chunks else None
