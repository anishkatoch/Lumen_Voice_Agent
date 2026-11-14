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
        # Set on every final (is_final) result - finish() waits on this after
        # sending Finalize instead of on the SDK's connection teardown, which
        # contains a fixed 0.5s sleep (_signal_exit) that used to be paid on
        # the critical path of every single turn.
        self._final_event = asyncio.Event()
        # Buffered alongside streaming to Deepgram so finish() can fall back
        # to OpenAI Whisper on the same audio if the live transcript is empty.
        self._raw_chunks: list[bytes] = []

    async def start(self):
        from app.admin.admin_config_service import get as get_config
        options = LiveOptions(
            model=get_config("stt_model") or STT_MODEL,
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
            # Signal finals even when the transcript text is empty - the
            # flush response to a Finalize message is often an empty final,
            # and finish() must not sit out its whole timeout waiting on it.
            if getattr(result, "is_final", False):
                self._final_event.set()
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

    async def _close_connection(self, conn) -> None:
        """Tear the websocket down off the critical path. The SDK's finish()
        includes a fixed 0.5s sleep before closing the socket - nothing after
        finalize needs the connection, so that cost must not be paid inline."""
        try:
            await conn.finish()
        except Exception as e:
            print(f"[Deepgram Stream] Background close error (non-fatal): {e}")

    def abort(self) -> None:
        """Discard this utterance: close the connection in the background
        without waiting for any transcript. For false-positive speech where
        finish() would block the media receive loop for nothing."""
        conn = self._connection
        self._connection = None
        self._raw_chunks = []
        if conn:
            try:
                asyncio.get_running_loop().create_task(self._close_connection(conn))
            except RuntimeError:
                pass

    async def finish(self) -> str | None:
        conn = self._connection
        self._connection = None
        if conn:
            if self._connected:
                try:
                    # Finalize makes Deepgram flush its remaining audio into a
                    # final result immediately. Waiting for that result (not
                    # for the connection teardown) is what gets the transcript
                    # back fastest; the 2s cap is a safety bound, the flush
                    # normally lands well under 500ms.
                    self._final_event.clear()
                    await conn.finalize()
                    await asyncio.wait_for(self._final_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    print("[Deepgram Stream] Finalize flush timed out - using transcript so far")
                except Exception as e:
                    print(f"[Deepgram Stream] Finalize failed (non-fatal): {e}")
            asyncio.create_task(self._close_connection(conn))
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
    from app.admin.admin_config_service import get as get_config
    if get_config("voice_pipeline") == "openai_classic":
        print("[STT] voice_pipeline=openai_classic - using OpenAI STT, not Deepgram")
        from app.voice.openai_whisper_service import StreamingWhisperTranscriber
        return StreamingWhisperTranscriber()
    if get_config("stt_provider") == "openai_whisper":
        print("[STT] admin config stt_provider=openai_whisper - using OpenAI STT, not Deepgram")
        from app.voice.openai_whisper_service import StreamingWhisperTranscriber
        return StreamingWhisperTranscriber()
    return StreamingTranscriber()


async def transcribe_audio_smart(audio_bytes: bytes) -> str | None:
    """Transcribe audio using the configured STT provider. Falls back to
    OpenAI Whisper if Deepgram fails or returns nothing."""
    from app.admin.admin_config_service import get as get_config
    if get_config("voice_pipeline") == "openai_classic":
        from app.voice.openai_whisper_service import transcribe_wav_whisper
        return await transcribe_wav_whisper(audio_bytes)
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
    from app.admin.admin_config_service import get as get_config
    client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
    options = PrerecordedOptions(
        model=get_config("stt_model") or STT_MODEL,
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
