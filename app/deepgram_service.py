import asyncio
from deepgram import DeepgramClient, PrerecordedOptions
from app.config import DEEPGRAM_API_KEY, STT_MODEL, STT_LANGUAGE, STT_TIMEOUT


async def transcribe_audio(audio_bytes: bytes) -> str | None:
    client = DeepgramClient(api_key=DEEPGRAM_API_KEY)

    options = PrerecordedOptions(
        model=STT_MODEL,
        language=STT_LANGUAGE,
        smart_format=True,
        punctuate=True,
    )

    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.listen.prerecorded.v("1").transcribe_file,
                {"buffer": audio_bytes, "mimetype": "audio/wav"},
                options,
            ),
            timeout=STT_TIMEOUT,
        )
        results = response.results
        if not results or not results.channels:
            return None
        alternatives = results.channels[0].alternatives
        if not alternatives:
            return None
        transcript = alternatives[0].transcript.strip()
        return transcript if transcript else None

    except asyncio.TimeoutError:
        return None
    except Exception as e:
        print(f"[Deepgram] Error: {e}")
        return None
