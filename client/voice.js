class VoiceCapture {
    constructor(wsClient) {
        this.wsClient = wsClient;
        this.audioContext = null;
        this.stream = null;
        this.workletNode = null;
        this.recording = false;
    }

    async start() {
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: { channelCount: 1, sampleRate: 16000 },
        });

        // Try 16kHz; browser may ignore and use native rate
        this.audioContext = new AudioContext({ sampleRate: 16000 });

        await this.audioContext.audioWorklet.addModule("pcm-processor.js");

        const source = this.audioContext.createMediaStreamSource(this.stream);
        this.workletNode = new AudioWorkletNode(
            this.audioContext,
            "pcm-processor"
        );

        this.workletNode.port.onmessage = (event) => {
            if (this.recording) {
                this.wsClient.sendAudio(event.data);
            }
        };

        source.connect(this.workletNode);
        // Connect to destination so worklet processes — use gain node to mute
        const gain = this.audioContext.createGain();
        gain.gain.value = 0;
        this.workletNode.connect(gain);
        gain.connect(this.audioContext.destination);

        // Tell server our actual sample rate
        this.wsClient.sendJSON({
            type: "config",
            sampleRate: this.audioContext.sampleRate,
        });

        this.recording = true;
    }

    stop() {
        this.recording = false;
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        this.workletNode = null;
    }
}

class AudioPlayer {
    constructor() {
        this.audioContext = null;
        this._currentSource = null;
    }

    _ensureContext() {
        if (!this.audioContext || this.audioContext.state === "closed") {
            this.audioContext = new AudioContext();
        }
        return this.audioContext;
    }

    /** Stop any audio currently playing */
    stopCurrent() {
        if (this._currentSource) {
            try { this._currentSource.stop(); } catch {}
            this._currentSource = null;
        }
        // Also cancel browser speech synthesis if active
        if ("speechSynthesis" in window) {
            speechSynthesis.cancel();
        }
    }

    async playWAV(arrayBuffer) {
        // Stop any previous playback to prevent overlapping voices
        this.stopCurrent();
        try {
            const ctx = this._ensureContext();
            if (ctx.state === "suspended") await ctx.resume();
            const audioBuffer = await ctx.decodeAudioData(arrayBuffer.slice(0));
            const source = ctx.createBufferSource();
            source.buffer = audioBuffer;
            source.connect(ctx.destination);
            this._currentSource = source;
            source.start();
            return new Promise((resolve) => {
                source.onended = () => {
                    this._currentSource = null;
                    resolve();
                };
            });
        } catch (e) {
            this._currentSource = null;
            console.error("Audio playback failed:", e);
        }
    }

    // Preferred female voice names, checked in priority order.
    // Covers macOS (Samantha, Karen, Victoria, Moira), Windows (Zira, Hazel),
    // and Android/Chrome (Google US English female).
    static _FEMALE_VOICE_NAMES = [
        "samantha", "karen", "victoria", "moira",       // macOS
        "zira", "hazel",                                  // Windows
        "google us english",                              // Chrome Android
        "female",                                         // generic fallback
    ];

    _pickFemaleVoice() {
        const voices = speechSynthesis.getVoices();
        if (!voices.length) return null;
        const lower = (v) => (v.name + " " + v.voiceURI).toLowerCase();
        for (const keyword of AudioPlayer._FEMALE_VOICE_NAMES) {
            const match = voices.find((v) => lower(v).includes(keyword));
            if (match) return match;
        }
        // Last resort: any en-US voice that isn't the system default male
        return voices.find((v) => v.lang.startsWith("en")) || voices[0];
    }

    speakText(text, onEnd) {
        if (!("speechSynthesis" in window)) {
            if (onEnd) onEnd();
            return;
        }

        const _speak = () => {
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.rate = 1.0;
            utterance.pitch = 1.0;
            const voice = this._pickFemaleVoice();
            if (voice) utterance.voice = voice;
            if (onEnd) utterance.onend = onEnd;
            speechSynthesis.speak(utterance);
        };

        // Voices may not be loaded yet — wait for the event if the list is empty
        if (speechSynthesis.getVoices().length > 0) {
            _speak();
        } else {
            speechSynthesis.addEventListener("voiceschanged", _speak, { once: true });
        }
    }
}

class WebSpeechFallback {
    constructor() {
        this.recognition = null;
        this.available =
            "webkitSpeechRecognition" in window ||
            "SpeechRecognition" in window;
    }

    start(onResult) {
        const SpeechRecognition =
            window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) return;

        this.recognition = new SpeechRecognition();
        this.recognition.continuous = false;
        this.recognition.interimResults = false;
        this.recognition.lang = "en-US";

        this.recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            onResult(transcript);
        };

        this.recognition.onerror = (event) => {
            console.error("Web Speech error:", event.error);
        };

        this.recognition.start();
    }

    stop() {
        if (this.recognition) {
            this.recognition.stop();
            this.recognition = null;
        }
    }
}
