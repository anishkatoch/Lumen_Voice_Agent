import asyncio
from deepgram import DeepgramClient, PrerecordedOptions, LiveOptions, LiveTranscriptionEvents
from app.core.config import DEEPGRAM_API_KEY, STT_MODEL, STT_LANGUAGE, STT_TIMEOUT


class StreamingTranscriber:
    """
    Opens a Deepgram live connection when speech starts, streams chunks as they
    arrive, and returns the final transcript when speech ends.
    Saves ~200-400ms vs sending the full file after silence is detected.
    """

    def __init__(self):
        self._client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
        self._connection = None
        self._connected = False
        self._transcript = ""
        self._last_partial = ""
        self._done = asyncio.Event()
        # Buffered alongside streaming to Deepgram so finish() can fall back
        # to OpenAI Whisper on the same audio if the live transcript is empty.
        self._raw_chunks: list[bytes] = []

    async def start(self):
        options = LiveOptions(
            model=STT_MODEL,
            language=STT_LANGUAGE,
            smart_format=True,
            punctuate=True,
            interim_results=True,
            encoding="linear16",
            sample_rate=16000,
            channels=1,
        )
        self._connection = self._client.listen.asynclive.v("1")

        async def on_transcript(_, result, **__):
            alt = result.channel.alternatives[0]
            if not alt.transcript:
                return
            # Tracked on every event (interim or final) so a caller can peek
            # at how the in-progress utterance currently reads, e.g. to
            # decide whether a pause looks like a real end-of-turn.
            self._last_partial = alt.transcript
            if result.is_final:
                self._transcript += (" " if self._transcript else "") + alt.transcript

        async def on_open(_, *__, **___):
            self._connected = True

        async def on_close(_, **__):
            self._done.set()

        async def on_error(_, error, **__):
            print(f"[Deepgram Stream] Error: {error}")
            self._done.set()

        self._connection.on(LiveTranscriptionEvents.Open, on_open)
        self._connection.on(LiveTranscriptionEvents.Transcript, on_transcript)
        self._connection.on(LiveTranscriptionEvents.Close, on_close)
        self._connection.on(LiveTranscriptionEvents.Error, on_error)

        # start() returns False (without raising or firing on_error) on auth
        # failures, bad handshakes, or network errors - if we don't check it,
        # every send() below silently no-ops and finish() blindly waits out
        # the full STT_TIMEOUT before falling back to Whisper.
        ok = await self._connection.start(options)
        if not ok:
            print("[Deepgram Stream] Connection failed to open - check DEEPGRAM_API_KEY and network/DNS to Deepgram")
            self._connection = None
        else:
            print("[Deepgram] Live stream connected - using Deepgram for this utterance")

    async def send(self, pcm_bytes: bytes):
        self._raw_chunks.append(pcm_bytes)
        if self._connection and self._connected:
            await self._connection.send(pcm_bytes)

    async def finish(self) -> str | None:
        if self._connection:
            await self._connection.finish()
            try:
                await asyncio.wait_for(self._done.wait(), timeout=STT_TIMEOUT)
            except asyncio.TimeoutError:
                pass
        result = self._transcript.strip()
        if result:
            print(f"[Deepgram] Live transcript: {result}")
            return result
        if self._raw_chunks:
            print("[Deepgram] Live stream produced no transcript - falling back to OpenAI Whisper")
            from app.voice.openai_whisper_service import transcribe_wav_whisper
            from app.voice.vad_service import build_wav_bytes
            wav_bytes = build_wav_bytes(b"".join(self._raw_chunks))
            return await transcribe_wav_whisper(wav_bytes)
        return None

    def get_last_partial(self) -> str | None:
        """Most recent transcript text seen for the in-progress utterance
        (interim or final), without waiting for `finish()`. Returns None if
        nothing has been recognized yet."""
        return self._last_partial or None


def get_streaming_transcriber():
    """Return a streaming transcriber for the configured STT provider."""
    from app.core.config import OPENAI_PIPELINE_ENABLED
    if OPENAI_PIPELINE_ENABLED:
        print("[STT] OPENAI_PIPELINE_ENABLED is set - using OpenAI Whisper, not Deepgram")
        from app.voice.openai_whisper_service import StreamingWhisperTranscriber
        return StreamingWhisperTranscriber()
    from app.admin.admin_config_service import get as get_config
    if get_config("stt_provider") == "openai_whisper":
        print("[STT] admin config stt_provider=openai_whisper - using OpenAI Whisper, not Deepgram")
        from app.voice.openai_whisper_service import StreamingWhisperTranscriber
        return StreamingWhisperTranscriber()
    return StreamingTranscriber()


async def transcribe_audio_smart(audio_bytes: bytes) -> str | None:
    """Transcribe audio using the configured STT provider. Falls back to
    OpenAI Whisper if Deepgram fails or returns nothing."""
    from app.core.config import OPENAI_PIPELINE_ENABLED
    if OPENAI_PIPELINE_ENABLED:
        from app.voice.openai_whisper_service import transcribe_wav_whisper
        return await transcribe_wav_whisper(audio_bytes)
    from app.admin.admin_config_service import get as get_config
    if get_config("stt_provider") == "openai_whisper":
        from app.voice.openai_whisper_service import transcribe_wav_whisper
        return await transcribe_wav_whisper(audio_bytes)
    transcript = await transcribe_audio(audio_bytes)
    if not transcript:
        print("[Deepgram] Prerecorded transcription failed/empty - falling back to OpenAI Whisper")
        from app.voice.openai_whisper_service import transcribe_wav_whisper
        transcript = await transcribe_wav_whisper(audio_bytes)
    return transcript


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
