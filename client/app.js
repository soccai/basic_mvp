document.addEventListener("DOMContentLoaded", async () => {
    let uiState = "idle"; // "idle" | "listening" | "session" | "timeline"
    let useServerSTT = true;
    let isSpeaking = false;
    let nudgeTimer = null;
    let idleResetTimer = null;

    const IDLE_PROMPTS = [
        "You have 3 priorities pending today.",
        "Your day is synced. Calendar, messages, tasks ready.",
        "What do you want to move forward right now?",
    ];
    let lastIdlePrompt = null;
    let currentGuidePrompt = IDLE_PROMPTS[0];
    let currentBotResponse = "";
    let playingChunksCount = 0;

    const GUIDE_NUDGE = "Say 'start' when you're ready.";
    const SESSION_COMPLETE_TEXT = "Done. Take a beat.";

    const wsUrl = `ws://${location.host}/ws`;
    const wsClient = new WebSocketClient(wsUrl);
    const voiceCapture = new VoiceCapture(wsClient);
    const audioPlayer = new AudioPlayer();
    const sessionSurface = new SessionSurface();
    const timelineSurface = new TimelineSurface();
    const webSpeechFallback = new WebSpeechFallback();

    // DOM elements
    const micButton = document.getElementById("mic-button");
    const statusText = document.getElementById("status-text");
    const timelineButton = document.getElementById("timeline-button");
    const endSessionButton = document.getElementById("end-session-button");
    const timelineCloseButton = document.getElementById("timeline-close");
    const listeningIndicator = document.getElementById("listening-indicator");
    const sessionMicButton = document.getElementById("session-mic-button");
    const sessionTextInput = document.getElementById("session-text");
    const connectionIdEl = document.getElementById("connection-id");
    const rejectionOverlay = document.getElementById("rejection-overlay");
    const retryButton = document.getElementById("retry-connection");

    function setSpeaking(value) {
        isSpeaking = value;
        if (value) {
            // AI is speaking: stop recording and disable mic buttons
            voiceCapture.recording = false;
            micButton.classList.add("disabled");
            sessionMicButton.classList.add("disabled");
            sessionMicButton.classList.remove("active");
        } else {
            // AI done speaking: re-enable mic buttons.
            micButton.classList.remove("disabled");
            sessionMicButton.classList.remove("disabled");
            
            // Auto-resume listening for natural, hands-free conversation flow
            if (uiState === "session") {
                voiceCapture.recording = true;
                sessionMicButton.classList.add("active");
            } else if (uiState === "listening") {
                voiceCapture.recording = true;
                micButton.classList.add("active");
                listeningIndicator.classList.remove("hidden");
            }
        }
    }

    function clearNudgeTimer() {
        if (nudgeTimer) {
            clearTimeout(nudgeTimer);
            nudgeTimer = null;
        }
    }

    function clearIdleResetTimer() {
        if (idleResetTimer) {
            clearTimeout(idleResetTimer);
            idleResetTimer = null;
        }
    }

    function pickPromptWithoutImmediateRepeat(options, lastPrompt) {
        if (options.length <= 1) return options[0] || "";
        const filtered = options.filter((option) => option !== lastPrompt);
        const pool = filtered.length > 0 ? filtered : options;
        return pool[Math.floor(Math.random() * pool.length)];
    }

    function setIdlePrompt() {
        currentGuidePrompt = pickPromptWithoutImmediateRepeat(IDLE_PROMPTS, lastIdlePrompt);
        lastIdlePrompt = currentGuidePrompt;
        statusText.textContent = currentGuidePrompt;
    }

    async function speakText(text, onEnd) {
        // Stop any currently playing audio to prevent overlap
        audioPlayer.stopCurrent();
        setSpeaking(true);
        try {
            const resp = await fetch("/api/tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text }),
            });
            if (resp.ok) {
                const buf = await resp.arrayBuffer();
                await audioPlayer.playWAV(buf);
            } else {
                // Server TTS unavailable — fall back to browser speech
                await new Promise((resolve) => audioPlayer.speakText(text, resolve));
            }
        } catch {
            await new Promise((resolve) => audioPlayer.speakText(text, resolve));
        }
        setSpeaking(false);
        if (onEnd) onEnd();
    }

    // After session completion, wait 6 s before resetting text to guide prompt
    // so the user has time to sit with the "You moved something forward." message.
    function scheduleIdlePromptReset(delay = 6000) {
        clearIdleResetTimer();
        idleResetTimer = setTimeout(() => {
            if (uiState === "idle" && !isSpeaking) {
                setIdlePrompt();
            }
        }, delay);
    }

    // Enter listening mode silently — don't speak the guide prompt immediately.
    // The user just finished a session (or is opening the mic for the first
    // time). Give them space to start talking. The nudge timer will voice the
    // prompt after 5 s of silence so they know the system is ready.
    async function beginListeningPrompt() {
        statusText.textContent = "Listening...";
        // Do NOT auto-speak here — just show the text and wait.
        if (uiState !== "listening") return;

        startNudgeTimer();

        if (!useServerSTT && webSpeechFallback.available) {
            webSpeechFallback.start((transcript) => {
                clearNudgeTimer();
                wsClient.sendJSON({ type: "text_input", text: transcript });
                statusText.textContent = `"${transcript}"`;
            });
        }
    }

    // Nudge: voice the guide prompt after 5 s of silence in listening mode.
    // This fires when the user hasn't spoken after opening the mic.
    function startNudgeTimer() {
        clearNudgeTimer();
        nudgeTimer = setTimeout(async () => {
            if (uiState === "listening" && !isSpeaking) {
                statusText.textContent = GUIDE_NUDGE;
                await speakText(GUIDE_NUDGE);
            }
        }, 5000);
    }

    async function checkHealth() {
        try {
            const resp = await fetch("/health");
            const data = await resp.json();
            useServerSTT = data.stt === "ready";
        } catch {
            useServerSTT = false;
        }
    }

    async function syncState() {
        try {
            const resp = await fetch("/api/state");
            const state = await resp.json();
            if (
                state.session_state === "session_active" ||
                state.session_state === "session_interrupted"
            ) {
                uiState = "session";
                sessionSurface.show(state.active_session_id);
            }
        } catch {
            // Server not ready yet
        }
    }

    wsClient.connect();

    // Connection gate events
    wsClient.on("connection_accepted", (msg) => {
        connectionIdEl.textContent = `Session: ${msg.connection_token.slice(0, 8)}`;
        rejectionOverlay.classList.add("hidden");
        document.getElementById("home-view").classList.remove("hidden");
    });

    wsClient.on("connection_rejected", (msg) => {
        rejectionOverlay.classList.remove("hidden");
        document.getElementById("home-view").classList.add("hidden");
        micButton.classList.add("disabled");
    });

    retryButton.addEventListener("click", () => {
        sessionStorage.removeItem("lifeos_connection_token");
        wsClient.rejected = false;
        wsClient.connect();
    });

    // Mic button (home screen)
    micButton.addEventListener("click", async () => {
        if (isSpeaking) return;

        if (uiState === "idle") {
            clearIdleResetTimer();
            if (useServerSTT) {
                await voiceCapture.start();
            }
            uiState = "listening";
            micButton.classList.add("active");
            listeningIndicator.classList.remove("hidden");
            beginListeningPrompt();
        } else if (uiState === "listening") {
            clearNudgeTimer();
            voiceCapture.stop();
            webSpeechFallback.stop();
            uiState = "idle";
            micButton.classList.remove("active");
            listeningIndicator.classList.add("hidden");
            setIdlePrompt();
            wsClient.sendJSON({ type: "flush_audio" });
        }
    });

    // Session mic button
    sessionMicButton.addEventListener("click", async () => {
        if (isSpeaking) return;
        if (uiState !== "session") return;

        if (sessionMicButton.classList.contains("active")) {
            voiceCapture.recording = false;
            sessionMicButton.classList.remove("active");
            wsClient.sendJSON({ type: "flush_audio" });
        } else {
            if (!voiceCapture.recording && !voiceCapture.stream) {
                await voiceCapture.start();
            }
            voiceCapture.recording = true;
            sessionMicButton.classList.add("active");
        }
    });

    endSessionButton.addEventListener("click", () => {
        wsClient.sendJSON({ type: "end_session" });
    });

    // Text input during session
    sessionTextInput.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        const text = sessionTextInput.value.trim();
        if (!text || uiState !== "session") return;
        sessionTextInput.value = "";
        wsClient.sendJSON({ type: "text_input", text });
    });

    timelineButton.addEventListener("click", () => {
        clearNudgeTimer();
        clearIdleResetTimer();
        voiceCapture.stop();
        webSpeechFallback.stop();
        micButton.classList.remove("active");
        listeningIndicator.classList.add("hidden");
        timelineSurface.show();
        uiState = "timeline";
    });

    timelineCloseButton.addEventListener("click", () => {
        timelineSurface.hide();
        uiState = "idle";
        setIdlePrompt();
    });

    // WebSocket events
    wsClient.on("connected", async () => {
        setIdlePrompt();
        await checkHealth();
        await syncState();
    });

    wsClient.on("disconnected", () => {
        if (wsClient.rejected) {
            // Rejected by connection gate — show overlay, don't show "Reconnecting"
            rejectionOverlay.classList.remove("hidden");
            document.getElementById("home-view").classList.add("hidden");
            return;
        }
        if (uiState === "session") {
            sessionSurface.showInterrupted();
        }
        statusText.textContent = "Reconnecting...";
    });

    wsClient.on("transcript", (msg) => {
        clearNudgeTimer();
        if (msg.text) {
            statusText.textContent = `"${msg.text}"`;
        }
    });

    wsClient.on("speech_started", () => {
        clearNudgeTimer();
    });

    wsClient.on("intent", (msg) => {
        if (msg.response_text) {
            currentBotResponse = msg.response_text;
            statusText.textContent = msg.response_text;
        } else {
            currentBotResponse = "";
        }
    });

    wsClient.on("bot_response_chunk", (msg) => {
        currentBotResponse += (currentBotResponse ? " " : "") + msg.text;
        statusText.textContent = currentBotResponse;
    });

    wsClient.on("audio", async (arrayBuffer) => {
        playingChunksCount++;
        setSpeaking(true);
        try {
            await audioPlayer.playWAV(arrayBuffer);
        } finally {
            playingChunksCount--;
            if (playingChunksCount === 0) {
                setSpeaking(false);
            }
        }
    });

    wsClient.on("audio_done", () => {
        // Playback resume handled by audio event above
    });

    wsClient.on("tts_text", async (msg) => {
        await speakText(msg.text);
    });

    // B + C: Session start — delay surface so TTS plays first
    wsClient.on("session_started", (msg) => {
        uiState = "session";
        clearNudgeTimer();
        micButton.classList.remove("active");
        listeningIndicator.classList.add("hidden");
        // Stop any ongoing recording — user must click mic to talk
        voiceCapture.recording = false;
        // Delay surface open so "Starting your session" TTS plays first
        setTimeout(() => {
            sessionSurface.show(msg.session_id, msg.intent_transcript);
            // Mic starts disabled — user clicks session mic button to record
            sessionMicButton.classList.remove("active");
        }, 600);
    });

    // E: Session end — show completion message sequence
    wsClient.on("session_completed", (msg) => {
        voiceCapture.stop();
        webSpeechFallback.stop();
        micButton.classList.remove("active");
        sessionMicButton.classList.remove("active");
        listeningIndicator.classList.add("hidden");
        sessionSurface.hide();
        uiState = "idle";
        clearNudgeTimer();
        clearIdleResetTimer();
        statusText.textContent = SESSION_COMPLETE_TEXT;
        scheduleIdlePromptReset();
    });

    wsClient.on("session_abandoned", () => {
        voiceCapture.stop();
        webSpeechFallback.stop();
        micButton.classList.remove("active");
        sessionMicButton.classList.remove("active");
        listeningIndicator.classList.add("hidden");
        sessionSurface.hide();
        uiState = "idle";
        statusText.textContent = "Session timed out.";

        setTimeout(() => {
            if (uiState === "idle") {
                setIdlePrompt();
            }
        }, 3000);
    });

    wsClient.on("session_resumed", (msg) => {
        uiState = "session";
        sessionSurface.show(msg.session_id);
        sessionSurface.showResumed();
    });

    wsClient.on("error", (err) => {
        console.error("WS error:", err);
    });

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/sw.js").catch(() => {});
    }
});
