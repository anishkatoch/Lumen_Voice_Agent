import asyncio
from deepgram import DeepgramClient, PrerecordedOptions, LiveOptions, LiveTranscriptionEvents
from app.config import DEEPGRAM_API_KEY, STT_MODEL, STT_LANGUAGE, STT_TIMEOUT


class StreamingTranscriber:
    """
    Opens a Deepgram live connection when speech starts, streams chunks as they
    arrive, and returns the final transcript when speech ends.
    Saves ~200-400ms vs sending the full file after silence is detected.
    """

    def __init__(self):
        self._client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
        self._connection = None
        self._transcript = ""
        self._done = asyncio.Event()

    async def start(self):
        options = LiveOptions(
            model=STT_MODEL,
            language=STT_LANGUAGE,
            smart_format=True,
            punctuate=True,
            interim_results=False,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )
        self._connection = self._client.listen.asynclive.v("1")

        async def on_transcript(_, result, **__):
            alt = result.channel.alternatives[0]
            if result.is_final and alt.transcript:
                self._transcript += (" " if self._transcript else "") + alt.transcript

        async def on_close(_, **__):
            self._done.set()

        async def on_error(_, error, **__):
            print(f"[Deepgram Stream] Error: {error}")
            self._done.set()

        self._connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
        self._connection.on(LiveTranscriptionEvents.Close, on_close)
        self._connection.on(LiveTranscriptionEvents.Error, on_error)

        await self._connection.start(options)

    async def send(self, pcm_bytes: bytes):
        if self._connection:
            await self._connection.send(pcm_bytes)

    async def finish(self) -> str | None:
        if self._connection:
            await self._connection.finish()
            try:
                await asyncio.wait_for(self._done.wait(), timeout=STT_TIMEOUT)
            except asyncio.TimeoutError:
                pass
        result = self._transcript.strip()
        return result if result else None


def get_streaming_transcriber():
    """Return a streaming transcriber for the configured STT provider."""
    from app.admin_config_service import get as get_config
    if get_config("stt_provider") == "openai_whisper":
        from app.openai_whisper_service import StreamingWhisperTranscriber
        return StreamingWhisperTranscriber()
    return StreamingTranscriber()


async def transcribe_audio_smart(audio_bytes: bytes) -> str | None:
    """Transcribe audio using the configured STT provider."""
    from app.admin_config_service import get as get_config
    if get_config("stt_provider") == "openai_whisper":
        from app.openai_whisper_service import transcribe_wav_whisper
        return await transcribe_wav_whisper(audio_bytes)
    return await transcribe_audio(audio_bytes)


async def transcribe_audio(audio_bytes: bytes) -> str | None:
    """Fallback: send full WAV file to Deepgram prerecorded API."""
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
