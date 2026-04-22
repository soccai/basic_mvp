import asyncio
import json
import logging

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
                # Ignore mic audio while a transcript/response task is still in
                # flight, including the short window before `processing` flips.
                if state.processing or (
                    state._current_task and not state._current_task.done()
                ):
                    continue

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

                # Fragment too short — discard buffer and keep listening
                if vad_result == "discard":
                    logger.debug("VAD: discarded short fragment, clearing buffer")
                    state.audio_buffer = bytearray()
                    state.speech_started = False
                    continue

                if vad_result == "speech_end" and len(state.audio_buffer) > 0:
                    logger.debug("VAD: speech_end — buffer %d bytes, starting STT",
                                 len(state.audio_buffer))
                    full_audio = prepare_for_stt(
                        bytes(state.audio_buffer), state.client_sample_rate
                    )

                    # Transcribe in executor to avoid blocking
                    transcript = ""
                    if stt and stt.ready:
                        loop = asyncio.get_running_loop()
                        transcript = await loop.run_in_executor(
                            None, stt.transcribe, full_audio
                        )
                    logger.debug("STT result: %r", transcript[:120] if transcript else "(empty)")

                    await websocket.send_json({
                        "type": "transcript",
                        "text": transcript,
                        "is_final": True,
                    })

                    # Route through governing loop (cancel any in-flight request)
                    if transcript and intent_router and session_manager and event_store:
                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, transcript,
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            connection_state=state,
                            identity_graph=identity_graph,
                        ))
                    else:
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
                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, transcript,
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            connection_state=state,
                            identity_graph=identity_graph,
                        ))

                elif msg_type == "end_session":
                    logger.debug("End session tap received")
                    if session_manager and intent_router and event_store:
                        # Drive to LISTENING for tap-to-end
                        if session_manager.state == SessionState.SESSION_ACTIVE:
                            session_manager.transition(SessionState.LISTENING)

                        await _cancel_and_replace(state, _handle_transcript(
                            websocket, "(tap to end)",
                            intent_router, session_manager, event_store,
                            tts, llm_responder,
                            force_intent="END_SESSION",
                            connection_state=state,
                            identity_graph=identity_graph,
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

        # Flush audio buffer before TTS — discard any audio captured during processing
        if connection_state:
            connection_state.audio_buffer = bytearray()
            connection_state.vad.reset()
            connection_state.speech_started = False
            logger.debug("Audio buffer flushed after response")

        # TTS delivery
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

        # Return state to resting position
        if session_manager.state == SessionState.INTENT_RESOLVED:
            if session_manager.active_session:
                session_manager.transition(SessionState.SESSION_ACTIVE)
            else:
                session_manager.transition(SessionState.IDLE)
        logger.debug("Resting state: %s", session_manager.state.value)

    except asyncio.CancelledError:
        logger.debug("Transcript task cancelled (superseded by new input): %r", transcript[:80])
        raise  # Re-raise so the task is properly marked as cancelled

    finally:
        # Release processing lock
        if connection_state:
            connection_state.processing = False
        logger.debug("Processing lock released")
