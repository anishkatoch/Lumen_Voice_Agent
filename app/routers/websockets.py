"""General (non-telephony) voice websocket pipelines - moved verbatim from
the pre-refactor app/main.py, same reasoning as the telephony websockets."""
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth.dependencies import decode_supabase_token
from app.core.config import (
    VAD_SENSITIVITY, VAD_BARGE_IN_SENSITIVITY, VAD_SILENCE_LIMIT, VAD_MIN_SPEECH,
    MEMORY_UPDATE_EVERY,
)

router = APIRouter(tags=["voice:websocket"])

# Speak a filler if RAG/KB lookup + LLM round-trip hasn't produced anything by
# then. Matches telephony_twilio.py / telephony_plivo.py.
RETRIEVAL_FILLER_DELAY_SECONDS = 1.2


@router.websocket("/ws/{token}")
async def websocket_endpoint(websocket: WebSocket, token: str):
    from app.core.websocket_manager import manager, UserSession, AgentState
    from app.conversations.conversation_repo import create_conversation, enforce_session_cap, cleanup_expired_sessions, set_call_status, set_call_duration_and_cost
    from app.conversations.message_repo import save_message, get_session_messages
    from app.voice.deepgram_service import StreamingTranscriber, get_streaming_transcriber, transcribe_audio_smart
    from app.conversations.memory_repo import get_user_memory, upsert_user_memory
    from app.voice.elevenlabs_service import text_to_speech_stream
    from app.agents.rag_service import retrieve_context, format_rag_context
    from app.voice.llm_service import generate_response, generate_response_stream, generate_memory_summary
    from app.voice.vad_service import detect_speech, build_wav_bytes

    try:
        payload = await decode_supabase_token(token)
        user_id = payload["sub"]
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"[WS AUTH FAILED] {type(e).__name__}: {e}")
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if manager.is_full():
        await websocket.close(code=1008, reason="Server at capacity")
        return

    await websocket.accept()

    # Session housekeeping - skip for guest users
    is_guest = payload.get("user_metadata", {}).get("is_guest", False)
    if not is_guest:
        from app.core.config import SESSION_MAX_PER_USER, SESSION_EXPIRY_DAYS
        await asyncio.to_thread(cleanup_expired_sessions, user_id, SESSION_EXPIRY_DAYS)
        await asyncio.to_thread(enforce_session_cap, user_id, SESSION_MAX_PER_USER)

    conversation = await asyncio.to_thread(create_conversation, user_id)
    conversation_id = conversation["id"]

    session = UserSession(
        user_id=user_id,
        websocket=websocket,
        conversation_id=conversation_id,
        sensitivity=VAD_SENSITIVITY,
    )
    manager.add_session(session)

    # Pre-load memory into local cache - avoids DB hit on every turn
    try:
        _initial_memory = await asyncio.to_thread(get_user_memory, user_id)
    except Exception as e:
        print(f"[WS] Failed to load memory: {e}")
        _initial_memory = None
    _memory_cache: dict = {"summary": _initial_memory}

    await manager.send_json(websocket, {"type": "ready", "conversation_id": conversation_id})

    async def speak_greeting(text: str):
        from app.voice.elevenlabs_service import text_to_speech_stream
        session.state = AgentState.SPEAKING
        await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
        await manager.send_json(websocket, {"type": "reply", "text": text})
        try:
            async for chunk in text_to_speech_stream(text):
                if session.state != AgentState.SPEAKING:
                    break
                await manager.send_bytes(websocket, chunk)
            await manager.send_json(websocket, {"type": "sentence_end"})
            await manager.send_json(websocket, {"type": "audio_end"})
        except Exception as e:
            print(f"[Greeting] TTS error: {e}")
        session.state = AgentState.LISTENING
        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    import random
    _greetings = [
        "Hey! How can I help you today?",
        "Hi there! What can I do for you?",
        "Hello! I'm here and listening. How can I assist you?",
        "Hey, good to have you here! What's on your mind?",
    ]
    asyncio.create_task(speak_greeting(random.choice(_greetings)))

    async def run_pipeline(pcm_bytes: bytes, sr: int = 16000, transcript_override: str | None = None, dg_stream=None):
        try:
            t0 = time.time()

            if transcript_override:
                transcript = transcript_override
                print(f"[Pipeline] Text input: {repr(transcript)}")
            elif dg_stream:
                # Streaming path: transcript was built in real-time - just finalize
                transcript = await dg_stream.finish()
                print(f"[Timing] STT (streaming): {time.time()-t0:.2f}s - {repr(transcript)}")
            else:
                # Fallback: send full audio to prerecorded API
                print(f"[Pipeline] Starting - audio={len(pcm_bytes)}B @ {sr}Hz")
                wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=sr)
                transcript = await transcribe_audio_smart(wav_bytes)
                print(f"[Timing] STT (prerecorded): {time.time()-t0:.2f}s - {repr(transcript)}")

            if not transcript:
                await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                session.state = AgentState.LISTENING
                return

            if not transcript_override:
                await manager.send_json(websocket, {"type": "transcript", "text": transcript})

            t1 = time.time()
            rag_chunks, session_history = await asyncio.gather(
                retrieve_context(transcript),
                asyncio.to_thread(get_session_messages, conversation_id),
            )
            async def _save_user_msg():
                try:
                    await asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript)
                except Exception as e:
                    print(f"[WS] Failed to save user message: {e}")
            asyncio.create_task(_save_user_msg())
            rag_context = format_rag_context(rag_chunks)
            long_term_memory = _memory_cache["summary"]  # use cached memory - no DB hit
            print(f"[Timing] RAG+History fetch: {time.time()-t1:.2f}s")

            # Stream LLM sentence by sentence → pipe each sentence to TTS immediately
            t2 = time.time()
            full_reply_parts: list[str] = []
            first_sentence = True

            session.state = AgentState.SPEAKING
            session.speaking_started_at = time.time()
            session.barge_in_armed = False  # only armed after frontend confirms playback started
            await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})

            try:
                async for sentence in generate_response_stream(
                    user_message=transcript,
                    session_history=session_history,
                    rag_context=rag_context,
                    long_term_memory=long_term_memory,
                ):
                    if session.state != AgentState.SPEAKING:
                        break  # barge-in happened

                    if first_sentence:
                        print(f"[Timing] LLM first sentence: {time.time()-t2:.2f}s - '{sentence}'")
                        first_sentence = False

                    full_reply_parts.append(sentence)

                    # Stream this sentence's TTS chunks immediately
                    t_tts = time.time()
                    chunk_count = 0
                    async for chunk in text_to_speech_stream(sentence):
                        if session.state != AgentState.SPEAKING:
                            break
                        if chunk_count == 0:
                            print(f"[Timing] TTS first chunk for sentence: {time.time()-t_tts:.2f}s")
                        chunk_count += 1
                        await manager.send_bytes(websocket, chunk)

                    # Tell frontend this sentence is complete - play it now
                    if session.state == AgentState.SPEAKING:
                        await manager.send_json(websocket, {"type": "sentence_end"})

            except asyncio.CancelledError:
                session.state = AgentState.LISTENING

            full_reply = " ".join(full_reply_parts)
            if full_reply:
                await manager.send_json(websocket, {"type": "reply", "text": full_reply})
                async def _save_assistant_msg(reply=full_reply):
                    try:
                        await asyncio.to_thread(save_message, conversation_id, user_id, "assistant", reply)
                    except Exception as e:
                        print(f"[WS] Failed to save assistant message: {e}")
                asyncio.create_task(_save_assistant_msg())
            session.message_count += 1
            print(f"[Timing] Total LLM+TTS: {time.time()-t2:.2f}s")

            if session.state == AgentState.SPEAKING:
                await manager.send_json(websocket, {"type": "audio_end"})
            else:
                await manager.send_json(websocket, {"type": "stop_audio"})

            if session.message_count % MEMORY_UPDATE_EVERY == 0:
                asyncio.create_task(update_memory(user_id, conversation_id))

        except Exception as e:
            print(f"[WS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            session.state = AgentState.LISTENING
            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    async def _analyze_session_sentiment(conv_id: str):
        """Background task - runs after session ends, never blocks the pipeline."""
        try:
            from app.voice.sentiment_service import analyze_sentiment
            from app.conversations.message_repo import get_session_messages
            from app.conversations.conversation_repo import update_conversation_sentiment

            messages = await asyncio.to_thread(get_session_messages, conv_id)
            if not messages:
                return

            # Build plain transcript: "User: ... Assistant: ..."
            lines = []
            for m in messages:
                role = "User" if m.get("role") == "user" else "Assistant"
                lines.append(f"{role}: {m.get('content', '')}")
            transcript = "\n".join(lines)

            result = await analyze_sentiment(transcript)
            await asyncio.to_thread(update_conversation_sentiment, conv_id, result)
        except Exception as e:
            print(f"[Sentiment] Task failed for {conv_id}: {e}")

    async def update_memory(uid: str, conv_id: str):
        try:
            old_summary = _memory_cache["summary"]
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            new_summary = await generate_memory_summary(old_summary, messages)
            await asyncio.to_thread(upsert_user_memory, uid, new_summary)
            _memory_cache["summary"] = new_summary
        except Exception as e:
            print(f"[WS] Failed to update memory: {e}")

    speech_buffer: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    sample_rate = 16000
    _dg_stream: StreamingTranscriber | None = None
    _ws_failure_reason: str | None = None  # set on genuine errors, surfaced in Call Analytics

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and data["bytes"]:
                audio_bytes = data["bytes"]

                if session.state == AgentState.SPEAKING:
                    # Only check barge-in AFTER frontend confirms playback has started.
                    # Before that, ambient mic noise / echo of the user's last utterance
                    # would trigger barge-in before a single LLM sentence is generated.
                    if not session.barge_in_armed:
                        continue
                    has_speech = await detect_speech(audio_bytes, session.barge_in_sensitivity)
                    if has_speech:
                        print("[BARGE-IN] Triggered!")
                        session.state = AgentState.LISTENING
                        session.barge_in_armed = False
                        await manager.send_json(websocket, {"type": "stop_audio"})
                        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                        speech_buffer.clear()
                        speech_buffer.append(audio_bytes)
                        silence_chunks = 0
                        speech_chunks = 1
                    continue

                if session.state == AgentState.PROCESSING:
                    continue

                if session.state == AgentState.LISTENING:
                    has_speech = await detect_speech(audio_bytes, session.sensitivity)
                    print(f"[VAD] chunk={len(audio_bytes)}B speech={has_speech} speech_chunks={speech_chunks} silence={silence_chunks}")

                    if has_speech:
                        speech_buffer.append(audio_bytes)
                        speech_chunks += 1
                        silence_chunks = 0
                        if speech_chunks == 1:
                            print("[VAD] Speech started - opening STT stream")
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            _dg_stream = get_streaming_transcriber()
                            try:
                                await _dg_stream.start()
                                # Send all buffered chunks (in case buffer had earlier chunks)
                                await _dg_stream.send(audio_bytes)
                            except Exception as e:
                                print(f"[Deepgram Stream] Failed to start: {e}")
                                _dg_stream = None
                        elif _dg_stream:
                            await _dg_stream.send(audio_bytes)
                    else:
                        if speech_chunks > 0:
                            silence_chunks += 1
                            speech_buffer.append(audio_bytes)
                            if _dg_stream:
                                await _dg_stream.send(audio_bytes)

                            if silence_chunks >= VAD_SILENCE_LIMIT:
                                print(f"[VAD] Utterance end - speech={speech_chunks} chunks")
                                if speech_chunks >= VAD_MIN_SPEECH:
                                    trimmed = speech_buffer[:speech_chunks + 1]
                                    full_audio = b"".join(trimmed)
                                    print(f"[VAD] Trimmed audio: {len(full_audio)}B")
                                    session.state = AgentState.PROCESSING
                                    asyncio.create_task(run_pipeline(full_audio, sample_rate, dg_stream=_dg_stream))
                                    _dg_stream = None
                                else:
                                    print("[VAD] Too short, discarding")
                                    if _dg_stream:
                                        asyncio.create_task(_dg_stream.finish())
                                        _dg_stream = None
                                    session.state = AgentState.LISTENING
                                    await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                                speech_buffer.clear()
                                silence_chunks = 0
                                speech_chunks = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    if msg.get("type") == "set_sensitivity":
                        session.sensitivity = float(msg.get("value", 0.5))
                    elif msg.get("type") == "sample_rate":
                        sample_rate = int(msg.get("value", 16000))
                        print(f"[WS] Client sample rate: {sample_rate}Hz")
                    elif msg.get("type") == "playback_started":
                        print("[WS] Frontend playback started - arming barge-in")
                        session.barge_in_armed = True
                    elif msg.get("type") == "playback_ended":
                        print("[WS] Frontend playback ended")
                        session.barge_in_armed = False
                        if session.state == AgentState.SPEAKING:
                            session.state = AgentState.LISTENING
                            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                    elif msg.get("type") == "text_input":
                        text = msg.get("text", "").strip()
                        if text and session.state == AgentState.LISTENING:
                            print(f"[WS] Text input: {repr(text)}")
                            session.state = AgentState.PROCESSING
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            await manager.send_json(websocket, {"type": "transcript", "text": text})
                            asyncio.create_task(run_pipeline(b"", sample_rate, transcript_override=text))
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except RuntimeError as e:
        if "disconnect message" not in str(e):
            print(f"[WS] Unexpected error: {e}")
            import traceback; traceback.print_exc()
            _ws_failure_reason = str(e)[:500]
    except Exception as e:
        print(f"[WS] Unexpected error: {e}")
        import traceback; traceback.print_exc()
        _ws_failure_reason = str(e)[:500]
    finally:
        manager.remove_session(session)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(update_memory(user_id, conversation_id))
            loop.create_task(_analyze_session_sentiment(conversation_id))
            loop.create_task(asyncio.to_thread(
                set_call_status, conversation_id,
                "failed" if _ws_failure_reason else "completed", _ws_failure_reason,
            ))
            loop.create_task(asyncio.to_thread(set_call_duration_and_cost, conversation_id))

@router.websocket("/ws/agent/{agent_id}/{token}")
async def agent_websocket_endpoint(websocket: WebSocket, agent_id: str, token: str, docs: str = ""):
    """Voice session tied to a specific agent - uses agent system prompt + RAG over agent docs."""
    from app.core.websocket_manager import manager, UserSession, AgentState
    from app.conversations.conversation_repo import create_conversation, enforce_session_cap, cleanup_expired_sessions, set_call_status, set_call_duration_and_cost
    from app.conversations.message_repo import save_message, get_session_messages
    from app.voice.deepgram_service import StreamingTranscriber, get_streaming_transcriber, transcribe_audio_smart
    from app.conversations.memory_repo import get_user_memory, upsert_user_memory
    from app.voice.elevenlabs_service import text_to_speech_stream
    from app.agents.agent_rag_service import retrieve_agent_context, format_agent_context
    from app.agents.agent_repo import get_agent
    from app.voice.llm_service import generate_response_stream, generate_memory_summary
    from app.voice.vad_service import detect_speech, build_wav_bytes
    from app.kb.user_kb_rag import retrieve_user_kb_context, format_user_kb_context

    # Auth
    try:
        payload = await decode_supabase_token(token)
        user_id = payload["sub"]
    except Exception:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Load agent + ownership check
    agent = await asyncio.to_thread(get_agent, agent_id)
    if not agent or agent["user_id"] != user_id:
        await websocket.close(code=4003, reason="Agent not found")
        return

    agent_sensitivity = float(agent.get("barge_in_sensitivity", VAD_BARGE_IN_SENSITIVITY))
    _hr = (agent.get("hr_instructions") or "").strip()
    _name = (agent.get("name") or "").strip()
    agent_system_prompt = (
        (f"Your name is {_name}. If asked your name, say {_name}.\n\n" if _name else "")
        + (agent.get("system_prompt") or "")
        + (f"\n\n--- HR INSTRUCTIONS ---\n{_hr}" if _hr else "")
    ) or None
    agent_kb_instructions = agent.get("kb_instructions") or None
    doc_ids = [d.strip() for d in docs.split(",") if d.strip()] if docs else []

    # Load active tool functions for this agent
    from app.agents.agent_repo import get_agent_functions
    _all_fns = await asyncio.to_thread(get_agent_functions, agent_id)
    _tool_functions = [f for f in _all_fns if f.get("url") and f.get("trigger_type") == "llm_tool_call"]

    async def _execute_webhook(fn: dict, args: dict) -> str:
        """Call the configured webhook and return its response as a JSON string."""
        import httpx
        method = (fn.get("method") or "POST").upper()
        url = fn["url"]
        headers = fn.get("headers") or {}
        timeout_s = (fn.get("timeout_ms") or 30000) / 1000
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as hclient:
                if method == "GET":
                    resp = await hclient.get(url, params=args, headers=headers)
                elif method == "DELETE":
                    resp = await hclient.delete(url, params=args, headers=headers)
                else:
                    resp = await hclient.request(method, url, json=args, headers=headers)
            try:
                return json.dumps(resp.json())
            except Exception:
                return json.dumps({"response": resp.text, "status": resp.status_code})
        except Exception as e:
            print(f"[Webhook] {fn['name']} error: {e}")
            return json.dumps({"error": str(e)})

    if manager.is_full():
        await websocket.close(code=1008, reason="Server at capacity")
        return

    await websocket.accept()

    # Session housekeeping
    is_guest = payload.get("user_metadata", {}).get("is_guest", False)
    if not is_guest:
        from app.core.config import SESSION_MAX_PER_USER, SESSION_EXPIRY_DAYS
        await asyncio.to_thread(cleanup_expired_sessions, user_id, SESSION_EXPIRY_DAYS)
        await asyncio.to_thread(enforce_session_cap, user_id, SESSION_MAX_PER_USER)

    # Create conversation linked to this agent
    from app.core.database import get_supabase
    db_sync = get_supabase()
    def _create_agent_conv():
        result = db_sync.table("conversations").insert({
            "user_id": user_id,
            "title": f"{agent['name']} session",
            "agent_id": agent_id,
        }).execute()
        return result.data[0]
    conversation = await asyncio.to_thread(_create_agent_conv)
    conversation_id = conversation["id"]

    session = UserSession(
        user_id=user_id,
        websocket=websocket,
        conversation_id=conversation_id,
        sensitivity=VAD_SENSITIVITY,
    )
    session.barge_in_sensitivity = agent_sensitivity
    manager.add_session(session)

    try:
        _initial_memory = await asyncio.to_thread(get_user_memory, user_id)
    except Exception:
        _initial_memory = None
    _memory_cache: dict = {"summary": _initial_memory}

    # RAG scope - frontend can update mid-session via set_rag_scope message
    _rag_scopes: list[str] = ["personal", "global"]  # default = both

    await manager.send_json(websocket, {
        "type": "ready",
        "conversation_id": conversation_id,
        "agent_id": agent_id,
        "agent_name": agent["name"],
    })

    async def speak_agent_greeting(text: str):
        from app.voice.elevenlabs_service import text_to_speech_stream
        session.state = AgentState.SPEAKING
        await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
        await manager.send_json(websocket, {"type": "reply", "text": text})
        try:
            async for chunk in text_to_speech_stream(text):
                if session.state != AgentState.SPEAKING:
                    break
                await manager.send_bytes(websocket, chunk)
            await manager.send_json(websocket, {"type": "sentence_end"})
            await manager.send_json(websocket, {"type": "audio_end"})
        except Exception as e:
            print(f"[Agent Greeting] TTS error: {e}")
        session.state = AgentState.LISTENING
        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    import random
    # Respect the agent's own "Agent speaks first"/"Opening message" settings,
    # same as the phone-call paths (telephony_plivo.py/telephony_twilio.py) -
    # this previously always spoke one of these hardcoded templates regardless
    # of first_message_enabled or whatever text was configured in first_message.
    if agent.get("first_message_enabled", True):
        _agent_greetings = [
            f"Hey! I'm {agent['name']}. How can I help you today?",
            f"Hi there! {agent['name']} here. What can I do for you?",
            f"Hello! I'm {agent['name']} and I'm ready to assist. What's on your mind?",
            f"Hey, good to have you here! I'm {agent['name']}. How can I assist you?",
        ]
        greeting_text = agent.get("first_message") or random.choice(_agent_greetings)
        asyncio.create_task(speak_agent_greeting(greeting_text))

    async def run_agent_pipeline(pcm_bytes: bytes, sr: int = 16000,
                                 transcript_override: str | None = None, dg_stream=None):
        _retrieval_filler_task: asyncio.Task | None = None
        try:
            t0 = time.time()
            if transcript_override:
                transcript = transcript_override
            elif dg_stream:
                transcript = await dg_stream.finish()
                print(f"[Agent Pipeline] STT: {time.time()-t0:.2f}s - {repr(transcript)}")
            else:
                wav_bytes = build_wav_bytes(pcm_bytes, sample_rate=sr)
                transcript = await transcribe_audio_smart(wav_bytes)

            if not transcript:
                await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                session.state = AgentState.LISTENING
                return

            if not transcript_override:
                await manager.send_json(websocket, {"type": "transcript", "text": transcript})

            from app.voice.llm_service import (
                build_system_prompt, build_openai_tool,
                detect_tool_call, stream_tool_result_response,
                stream_text_as_sentences, get_filler_phrases,
            )

            full_reply_parts: list[str] = []

            async def _speak_filler_audio(text: str):
                session.state = AgentState.SPEAKING
                session.speaking_started_at = time.time()
                session.barge_in_armed = False
                await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})
                async for chunk in text_to_speech_stream(text):
                    if session.state != AgentState.SPEAKING:
                        break
                    await manager.send_bytes(websocket, chunk)
                if session.state == AgentState.SPEAKING:
                    await manager.send_json(websocket, {"type": "sentence_end"})

            async def _cover_retrieval_delay():
                # RAG/KB lookups and LLM round-trips can occasionally run long
                # before anything is said. If nothing real has been spoken
                # within this window, say a filler phrase so the user doesn't
                # hear dead air, mirroring the phone-call pipelines
                # (telephony_twilio.py / telephony_plivo.py). On anything
                # fast this never fires, so it no longer speaks on every
                # turn the way the old unconditional pre-tool-call filler did.
                await asyncio.sleep(RETRIEVAL_FILLER_DELAY_SECONDS)
                filler = random.choice(get_filler_phrases(agent))
                full_reply_parts.append(filler)
                await _speak_filler_audio(filler)

            def _cancel_retrieval_filler():
                if _retrieval_filler_task and not _retrieval_filler_task.done():
                    _retrieval_filler_task.cancel()

            _retrieval_filler_task = asyncio.create_task(_cover_retrieval_delay())

            t1 = time.time()
            gather_tasks = [
                asyncio.to_thread(retrieve_agent_context, transcript, agent_id, user_id, _rag_scopes),
                asyncio.to_thread(get_session_messages, conversation_id),
            ]
            if doc_ids:
                gather_tasks.append(asyncio.to_thread(retrieve_user_kb_context, transcript, doc_ids))
            gather_results = await asyncio.gather(*gather_tasks)
            rag_chunks = gather_results[0]
            session_history = gather_results[1]
            user_kb_chunks = gather_results[2] if doc_ids else []
            asyncio.create_task(asyncio.to_thread(save_message, conversation_id, user_id, "user", transcript))

            agent_rag_context = format_agent_context(rag_chunks)
            user_kb_context_str = format_user_kb_context(user_kb_chunks)
            rag_context = "\n\n".join(filter(None, [agent_rag_context, user_kb_context_str]))
            long_term_memory = _memory_cache["summary"]
            print(f"[Agent Pipeline] RAG+History: {time.time()-t1:.2f}s, scopes={_rag_scopes}, kb_docs={len(doc_ids)}")

            session.state = AgentState.SPEAKING
            session.speaking_started_at = time.time()
            session.barge_in_armed = False
            await manager.send_json(websocket, {"type": "state", "state": "SPEAKING"})

            system_str = build_system_prompt(
                agent_system_prompt, long_term_memory, rag_context, agent_kb_instructions,
                has_kb=bool(doc_ids),
            )
            conv_messages = list(session_history) + [{"role": "user", "content": transcript}]

            async def _tts_stream_sentences(sentence_gen):
                """Stream sentences through TTS, appending to full_reply_parts."""
                first = True
                t2 = time.time()
                try:
                    async for sentence in sentence_gen:
                        if session.state != AgentState.SPEAKING:
                            break
                        if first:
                            print(f"[Agent Pipeline] LLM first sentence: {time.time()-t2:.2f}s")
                            _cancel_retrieval_filler()
                            first = False
                        full_reply_parts.append(sentence)
                        t_tts = time.time()
                        n = 0
                        async for chunk in text_to_speech_stream(sentence):
                            if session.state != AgentState.SPEAKING:
                                break
                            if n == 0:
                                print(f"[Agent Pipeline] TTS first chunk: {time.time()-t_tts:.2f}s")
                            n += 1
                            await manager.send_bytes(websocket, chunk)
                        if session.state == AgentState.SPEAKING:
                            await manager.send_json(websocket, {"type": "sentence_end"})
                except asyncio.CancelledError:
                    session.state = AgentState.LISTENING

            try:
                if _tool_functions:
                    # ── Tool-call path ──────────────────────────────────────────
                    tools = [build_openai_tool(f) for f in _tool_functions]
                    text, tool_name, tool_args, call_id = await detect_tool_call(
                        conv_messages, system_str, tools
                    )
                    if tool_name and call_id:
                        print(f"[Agent Pipeline] Tool call: {tool_name}({tool_args})")

                        # Execute the webhook - the retrieval filler (already
                        # running) covers this if it's slow; nothing extra
                        # to speak here.
                        fn = next((f for f in _tool_functions if f["name"] == tool_name), None)
                        tool_result = json.dumps({"error": "function not found"})
                        if fn:
                            tool_result = await _execute_webhook(fn, tool_args)
                            print(f"[Agent Pipeline] Webhook result: {tool_result[:120]}")

                        # Second LLM call streams the actual answer using the tool result
                        await _tts_stream_sentences(
                            stream_tool_result_response(
                                conv_messages, system_str, tool_name, call_id, tool_args, tool_result
                            )
                        )
                    else:
                        # LLM chose to answer directly without calling any tool
                        await _tts_stream_sentences(
                            stream_text_as_sentences(text or "")
                        )
                else:
                    # ── Normal streaming path (no tools configured) ─────────────
                    await _tts_stream_sentences(
                        generate_response_stream(
                            user_message=transcript,
                            session_history=session_history,
                            rag_context=rag_context,
                            long_term_memory=long_term_memory,
                            system_prompt_override=agent_system_prompt,
                            kb_instructions=agent_kb_instructions,
                        )
                    )
            except asyncio.CancelledError:
                session.state = AgentState.LISTENING
            finally:
                _cancel_retrieval_filler()

            full_reply = " ".join(full_reply_parts).strip()
            if full_reply:
                await manager.send_json(websocket, {"type": "reply", "text": full_reply})
                asyncio.create_task(asyncio.to_thread(save_message, conversation_id, user_id, "assistant", full_reply))
            session.message_count += 1

            if session.state == AgentState.SPEAKING:
                await manager.send_json(websocket, {"type": "audio_end"})
            else:
                await manager.send_json(websocket, {"type": "stop_audio"})

            if session.message_count % MEMORY_UPDATE_EVERY == 0:
                asyncio.create_task(_update_agent_memory(user_id, conversation_id))

        except Exception as e:
            if _retrieval_filler_task and not _retrieval_filler_task.done():
                _retrieval_filler_task.cancel()
            print(f"[Agent WS] Pipeline error: {e}")
            import traceback; traceback.print_exc()
            session.state = AgentState.LISTENING
            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})

    async def _update_agent_memory(uid: str, conv_id: str):
        try:
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            new_summary = await generate_memory_summary(_memory_cache["summary"], messages)
            await asyncio.to_thread(upsert_user_memory, uid, new_summary)
            _memory_cache["summary"] = new_summary
        except Exception as e:
            print(f"[Agent WS] Memory update failed: {e}")

    async def _analyze_agent_session_sentiment(conv_id: str):
        try:
            from app.voice.sentiment_service import analyze_sentiment
            from app.conversations.conversation_repo import update_conversation_sentiment
            messages = await asyncio.to_thread(get_session_messages, conv_id)
            if not messages:
                return
            lines = []
            for m in messages:
                role = "User" if m.get("role") == "user" else "Assistant"
                lines.append(f"{role}: {m.get('content', '')}")
            result = await analyze_sentiment("\n".join(lines))
            await asyncio.to_thread(update_conversation_sentiment, conv_id, result)
        except Exception as e:
            print(f"[Agent Sentiment] Failed for {conv_id}: {e}")

    speech_buffer: list[bytes] = []
    silence_chunks = 0
    speech_chunks = 0
    sample_rate = 16000
    _dg_stream: StreamingTranscriber | None = None
    _ws_failure_reason: str | None = None  # set on genuine errors, surfaced in Call Analytics

    try:
        while True:
            data = await websocket.receive()

            if "bytes" in data and data["bytes"]:
                audio_bytes = data["bytes"]

                if session.state == AgentState.SPEAKING:
                    if not session.barge_in_armed:
                        continue
                    has_speech = await detect_speech(audio_bytes, session.barge_in_sensitivity)
                    if has_speech:
                        session.state = AgentState.LISTENING
                        session.barge_in_armed = False
                        await manager.send_json(websocket, {"type": "stop_audio"})
                        await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                        speech_buffer.clear()
                        speech_buffer.append(audio_bytes)
                        silence_chunks = 0
                        speech_chunks = 1
                    continue

                if session.state == AgentState.PROCESSING:
                    continue

                if session.state == AgentState.LISTENING:
                    has_speech = await detect_speech(audio_bytes, session.sensitivity)
                    if has_speech:
                        speech_buffer.append(audio_bytes)
                        speech_chunks += 1
                        silence_chunks = 0
                        if speech_chunks == 1:
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            _dg_stream = get_streaming_transcriber()
                            try:
                                await _dg_stream.start()
                                await _dg_stream.send(audio_bytes)
                            except Exception as e:
                                print(f"[Agent Deepgram] Stream failed: {e}")
                                _dg_stream = None
                        elif _dg_stream:
                            await _dg_stream.send(audio_bytes)
                    else:
                        if speech_chunks > 0:
                            silence_chunks += 1
                            speech_buffer.append(audio_bytes)
                            if _dg_stream:
                                await _dg_stream.send(audio_bytes)
                            if silence_chunks >= VAD_SILENCE_LIMIT:
                                if speech_chunks >= VAD_MIN_SPEECH:
                                    trimmed = speech_buffer[:speech_chunks + 1]
                                    full_audio = b"".join(trimmed)
                                    session.state = AgentState.PROCESSING
                                    asyncio.create_task(run_agent_pipeline(full_audio, sample_rate, dg_stream=_dg_stream))
                                    _dg_stream = None
                                else:
                                    if _dg_stream:
                                        asyncio.create_task(_dg_stream.finish())
                                        _dg_stream = None
                                    session.state = AgentState.LISTENING
                                    await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                                speech_buffer.clear()
                                silence_chunks = 0
                                speech_chunks = 0

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")
                    if msg_type == "set_sensitivity":
                        session.sensitivity = float(msg.get("value", 0.5))
                    elif msg_type == "set_barge_in_sensitivity":
                        session.barge_in_sensitivity = float(msg.get("value", agent_sensitivity))
                    elif msg_type == "set_rag_scope":
                        scope = msg.get("scope", "both")
                        if scope == "both":
                            _rag_scopes[:] = ["personal", "global"]
                        elif scope in ("personal", "global"):
                            _rag_scopes[:] = [scope]
                        print(f"[Agent WS] RAG scope updated: {_rag_scopes}")
                    elif msg_type == "sample_rate":
                        sample_rate = int(msg.get("value", 16000))
                    elif msg_type == "playback_started":
                        session.barge_in_armed = True
                    elif msg_type == "playback_ended":
                        session.barge_in_armed = False
                        if session.state == AgentState.SPEAKING:
                            session.state = AgentState.LISTENING
                            await manager.send_json(websocket, {"type": "state", "state": "LISTENING"})
                    elif msg_type == "text_input":
                        text = msg.get("text", "").strip()
                        if text and session.state == AgentState.LISTENING:
                            session.state = AgentState.PROCESSING
                            await manager.send_json(websocket, {"type": "state", "state": "PROCESSING"})
                            await manager.send_json(websocket, {"type": "transcript", "text": text})
                            asyncio.create_task(run_agent_pipeline(b"", sample_rate, transcript_override=text))
                except json.JSONDecodeError:
                    pass

    except (WebSocketDisconnect, ConnectionResetError):
        pass
    except RuntimeError as e:
        if "disconnect message" not in str(e):
            print(f"[Agent WS] Error: {e}")
            import traceback; traceback.print_exc()
            _ws_failure_reason = str(e)[:500]
    except Exception as e:
        print(f"[Agent WS] Error: {e}")
        import traceback; traceback.print_exc()
        _ws_failure_reason = str(e)[:500]
    finally:
        manager.remove_session(session)
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_update_agent_memory(user_id, conversation_id))
            loop.create_task(_analyze_agent_session_sentiment(conversation_id))
            loop.create_task(asyncio.to_thread(
                set_call_status, conversation_id,
                "failed" if _ws_failure_reason else "completed", _ws_failure_reason,
            ))
            loop.create_task(asyncio.to_thread(set_call_duration_and_cost, conversation_id))
