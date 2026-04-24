import asyncio
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

from server.voice.audio import pcm16_bytes_to_float32, prepare_for_stt
from server.voice.vad import SimpleVAD
from server.session.models import SessionState
from server.governing_loop import execute_governing_loop
from server import config

logger = logging.getLogger(__name__)

MAX_AUDIO_BUFFER_BYTES = 5 * 1024 * 1024  # 5 MB


class ConnectionState:
    def __init__(self):
        self.audio_buffer = bytearray()
        self.vad = SimpleVAD()
        self.client_sample_rate: int = config.STT_SAMPLE_RATE
        self.speech_started: bool = False
        self.processing: bool = False  # Lock: true while handling a transcript
        self._current_task: asyncio.Task | None = None  # In-flight _handle_transcript task


def _task_error_handler(task: asyncio.Task):
    """Log unhandled exceptions from background transcript tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Transcript task failed: %s", exc, exc_info=exc)


async def _cancel_and_replace(state: ConnectionState, coro) -> asyncio.Task:
    """Cancel any in-flight _handle_transcript task and start a new one."""
    if state._current_task and not state._current_task.done():
        logger.debug("Cancelling in-flight transcript task")
        state._current_task.cancel()
        try:
            await state._current_task
        except asyncio.CancelledError:
            pass
        logger.debug("Previous task cancelled")
    task = asyncio.create_task(coro)
    task.add_done_callback(_task_error_handler)
    state._current_task = task
    return task


async def websocket_handler(websocket: WebSocket):
    await websocket.accept()
    state = ConnectionState()
    logger.debug("WS connected from %s", websocket.client)

    app = websocket.app
    stt = getattr(app.state, "stt", None)
    tts = getattr(app.state, "tts", None)
    session_manager = getattr(app.state, "session_manager", None)
    intent_router = getattr(app.state, "intent_router", None)
    event_store = getattr(app.state, "event_store", None)
    llm_responder = getattr(app.state, "llm_responder", None)
    connection_gate = getattr(app.state, "connection_gate", None)
    identity_graph = getattr(app.state, "identity_graph", None)

    # --- Connection gate: enforce single-connection ---
    if connection_gate:
        client_token = websocket.query_params.get("token")
        accepted, token, reason = await connection_gate.try_acquire(
            client_token, websocket
        )
        if not accepted:
            await websocket.send_json({
                "type": "connection_rejected",
                "reason": reason,
            })
            await websocket.close(code=4409)
            return
        await websocket.send_json({
            "type": "connection_accepted",
            "connection_token": token,
        })

    try:
        # If a session was interrupted, resume it
        if session_manager and session_manager.state == SessionState.SESSION_INTERRUPTED:
            logger.debug("WS reconnect detected — resuming interrupted session")
            await session_manager.handle_reconnect()
            if session_manager.active_session:
                await websocket.send_json({
                    "type": "session_resumed",
                    "session_id": session_manager.active_session.session_id,
                })

        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(code=message.get("code", 1000))

            if "bytes" in message:
                chunk_bytes = message["bytes"]

                state.audio_buffer.extend(chunk_bytes)
                logger.debug("Audio chunk: %d bytes, buffer total: %d bytes",
                             len(chunk_bytes), len(state.audio_buffer))

                if len(state.audio_buffer) > MAX_AUDIO_BUFFER_BYTES:
                    logger.warning("Audio buffer exceeded %d bytes, resetting", MAX_AUDIO_BUFFER_BYTES)
                    state.audio_buffer = bytearray()
                    state.vad.reset()
                    state.speech_started = False
                    continue

                chunk_float = pcm16_bytes_to_float32(chunk_bytes)
                vad_result = state.vad.process_chunk(chunk_float)

                # Transition to LISTENING on first speech detection
                if vad_result == "speech" and not state.speech_started:
                    logger.debug("VAD: speech detected — transitioning to LISTENING")
                    state.speech_started = True
                    if session_manager and session_manager.state in (
                        SessionState.IDLE, SessionState.SESSION_ACTIVE,
                    ):
                        session_manager.transition(SessionState.LISTENING)
                    await websocket.send_json({"type": "speech_started"})

                # Fragment too short — discard buffer and keep listening
                if vad_result == "discard":
                    logger.debug("VAD: discarded short fragment, clearing buffer")
                    state.audio_buffer = bytearray()
                    state.speech_started = False
                    continue

                if vad_result == "speech_end" and len(state.audio_buffer) > 0:
                    logger.debug("VAD: speech_end — buffer %d bytes, starting STT",
                                 len(state.audio_buffer))
                    state.speech_started = False
                    
                    full_audio = prepare_for_stt(
                        bytes(state.audio_buffer), state.client_sample_rate
                    )
                    # Clear the buffer so new speech during STT/LLM starts fresh
                    state.audio_buffer = bytearray()

                    # Transcribe in executor to avoid blocking
                    transcript = ""
                    stt_start = time.perf_counter()
                    if stt and stt.ready:
                        loop = asyncio.get_running_loop()
                        transcript = await loop.run_in_executor(
                            None, stt.transcribe, full_audio
                        )
                    stt_ms = int((time.perf_counter() - stt_start) * 1000)
                    logger.debug("STT result: %r", transcript[:120] if transcript else "(empty)")

                    await websocket.send_json({
                        "type": "transcript",
                        "text": transcript,
                        "is_final": True,
                    })

                    # Route through governing loop (cancel any in-flight request)
                    if transcript and intent_router and session_manager and event_store:
                        latency_tracker = {"stt_ms": stt_ms}
                        turn_start = time.perf_counter()
                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, transcript,
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            connection_state=state,
                            identity_graph=identity_graph,
                            latency_tracker=latency_tracker,
                            turn_start=turn_start,
                        ))
                    else:
                        if not transcript:
                            logger.debug(
                                "Skipping transcript handling: empty transcript after STT (likely silence/noise)"
                            )
                        state.audio_buffer = bytearray()
                        state.vad.reset()
                        state.speech_started = False

            elif "text" in message:
                data = json.loads(message["text"])
                msg_type = data.get("type", "")
                logger.debug("WS text message: type=%s", msg_type)

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

                elif msg_type == "config":
                    rate = data.get("sampleRate")
                    if rate and isinstance(rate, (int, float)):
                        state.client_sample_rate = int(rate)
                        logger.debug("Client sample rate set to %d", state.client_sample_rate)

                elif msg_type == "flush_audio":
                    logger.debug("Client requested audio flush")
                    state.audio_buffer = bytearray()
                    state.vad.reset()
                    state.speech_started = False

                elif msg_type == "text_input":
                    transcript = data.get("text", "").strip()
                    logger.debug("Text input received: %r", transcript[:120])
                    if transcript and intent_router and session_manager and event_store:
                        # Drive to LISTENING for text input
                        if session_manager.state in (
                            SessionState.IDLE, SessionState.SESSION_ACTIVE,
                        ):
                            session_manager.transition(SessionState.LISTENING)

                        await websocket.send_json({
                            "type": "transcript",
                            "text": transcript,
                            "is_final": True,
                        })
                        latency_tracker = {"stt_ms": 0}
                        turn_start = time.perf_counter()
                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, transcript,
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            connection_state=state,
                            identity_graph=identity_graph,
                            latency_tracker=latency_tracker,
                            turn_start=turn_start,
                        ))

                elif msg_type == "end_session":
                    logger.debug("End session tap received")
                    if session_manager and intent_router and event_store:
                        # Drive to LISTENING for tap-to-end
                        if session_manager.state == SessionState.SESSION_ACTIVE:
                            session_manager.transition(SessionState.LISTENING)

                        latency_tracker = {"stt_ms": 0}
                        turn_start = time.perf_counter()
                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, "(tap to end)",
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            force_intent="END_SESSION",
                            connection_state=state,
                            identity_graph=identity_graph,
                            latency_tracker=latency_tracker,
                            turn_start=turn_start,
                        ))

    except WebSocketDisconnect:
        logger.debug("WS disconnected (state=%s)", session_manager.state.value if session_manager else "n/a")
        if connection_gate:
            await connection_gate.release()
        if session_manager:
            await session_manager.handle_disconnect()
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "message": "An internal error occurred. Disconnecting.",
            })
            await websocket.close(code=1011)
        except Exception:
            pass  # Client may already be disconnected
        if connection_gate:
            await connection_gate.release()
        if session_manager:
            await session_manager.handle_disconnect()


async def _handle_transcript(
    websocket: WebSocket,
    transcript: str,
    intent_router,
    session_manager,
    event_store,
    tts,
    llm_responder=None,
    force_intent: str | None = None,
    connection_state: ConnectionState | None = None,
    identity_graph=None,
    latency_tracker: dict | None = None,
    turn_start: float | None = None,
):
    # Set processing lock — prevents audio/text from triggering a second response
    if connection_state:
        connection_state.processing = True
    logger.debug("Processing lock acquired — transcript: %r, force_intent: %s",
                 transcript[:80], force_intent)

    try:
        # Drive voice-loop states: LISTENING -> PROCESSING -> INTENT_RESOLVED
        if session_manager.state == SessionState.LISTENING:
            session_manager.transition(SessionState.PROCESSING)
        if session_manager.state == SessionState.PROCESSING:
            session_manager.transition(SessionState.INTENT_RESOLVED)

        # Execute the 12-step governing loop
        ctx = await execute_governing_loop(
            transcript=transcript,
            session_manager=session_manager,
            intent_router=intent_router,
            event_store=event_store,
            llm_responder=llm_responder,
            force_intent=force_intent,
            identity_graph=identity_graph,
            latency_tracker=latency_tracker,
        )

        # Send session lifecycle event if any
        if ctx.session_event:
            logger.debug("Sending session event: %s", ctx.session_event.get("type"))
            await websocket.send_json(ctx.session_event)

        # Send intent info
        logger.debug("Sending intent response: intent=%s, text=%r",
                     ctx.intent_result.intent.value, ctx.response_text[:80])
        await websocket.send_json({
            "type": "intent",
            "intent": ctx.intent_result.intent.value,
            "response_text": ctx.response_text,
        })

        # Flush audio buffer before TTS, but preserve any barge-in audio
        if connection_state:
            if connection_state.speech_started:
                logger.debug("User is speaking, preserving audio buffer for barge-in")
            else:
                connection_state.audio_buffer = bytearray()
                connection_state.vad.reset()
                logger.debug("Audio buffer flushed after response")

        # TTS delivery
        tts_start = time.perf_counter()
        
        if ctx.response_stream:
            loop = asyncio.get_running_loop()
            full_text = ""
            current_sentence = ""
            import re
            
            async for chunk in ctx.response_stream:
                full_text += chunk
                current_sentence += chunk
                
                while True:
                    match = re.search(r'([.?!]\s+|\n+)', current_sentence)
                    if not match:
                        break
                    
                    end_idx = match.end()
                    clean_sentence = current_sentence[:end_idx].strip()
                    if clean_sentence:
                        if tts and tts.ready:
                            wav_bytes = await loop.run_in_executor(None, tts.synthesize, clean_sentence)
                            if wav_bytes:
                                logger.debug("TTS chunk: sending %d WAV bytes", len(wav_bytes))
                                await websocket.send_bytes(wav_bytes)
                        await websocket.send_json({"type": "bot_response_chunk", "text": clean_sentence})
                    
                    current_sentence = current_sentence[end_idx:]
            
            # Send any remaining text
            clean_sentence = current_sentence.strip()
            if clean_sentence:
                if tts and tts.ready:
                    wav_bytes = await loop.run_in_executor(None, tts.synthesize, clean_sentence)
                    if wav_bytes:
                        logger.debug("TTS chunk: sending %d WAV bytes", len(wav_bytes))
                        await websocket.send_bytes(wav_bytes)
                await websocket.send_json({"type": "bot_response_chunk", "text": clean_sentence})
                
            await websocket.send_json({"type": "audio_done"})
            
            # Store full response in ephemeral interaction
            if session_manager.active_session and session_manager.active_session.interactions:
                session_manager.active_session.interactions[-1]["response"] = full_text.strip()
                
        else:
            if tts and tts.ready and ctx.response_text:
                loop = asyncio.get_running_loop()
                wav_bytes = await loop.run_in_executor(None, tts.synthesize, ctx.response_text)
                if wav_bytes:
                    logger.debug("TTS: sending %d WAV bytes", len(wav_bytes))
                    await websocket.send_bytes(wav_bytes)
                    await websocket.send_json({"type": "audio_done"})
                else:
                    logger.debug("TTS: empty output, falling back to tts_text")
                    await websocket.send_json({"type": "tts_text", "text": ctx.response_text})
            elif ctx.response_text:
                logger.debug("TTS unavailable, sending tts_text fallback")
                await websocket.send_json({"type": "tts_text", "text": ctx.response_text})

        if latency_tracker is not None:
            latency_tracker["tts_ms"] = int((time.perf_counter() - tts_start) * 1000)

        # Return state to resting position
        if session_manager.state == SessionState.INTENT_RESOLVED:
            if session_manager.active_session:
                session_manager.transition(SessionState.SESSION_ACTIVE)
            else:
                session_manager.transition(SessionState.IDLE)
        logger.debug("Resting state: %s", session_manager.state.value)

        if turn_start is not None and latency_tracker is not None:
            turn_total_ms = int((time.perf_counter() - turn_start) * 1000)
            stt_ms = latency_tracker.get("stt_ms", 0)
            total_ms = turn_total_ms + stt_ms
            logger.info(
                "Turn completed in %dms (STT: %dms, Intent: %dms, LLM: %dms, TTS: %dms)",
                total_ms,
                stt_ms,
                latency_tracker.get("intent_ms", 0),
                latency_tracker.get("llm_ms", 0),
                latency_tracker.get("tts_ms", 0),
            )

    except asyncio.CancelledError:
        logger.debug("Transcript task cancelled (superseded by new input): %r", transcript[:80])
        raise  # Re-raise so the task is properly marked as cancelled

    finally:
        # Release processing lock
        if connection_state:
            connection_state.processing = False
        logger.debug("Processing lock released")
