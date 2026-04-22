class SessionSurface {
    constructor() {
        this.element = document.getElementById("session-surface");
        this.timerElement = document.getElementById("session-timer");
        this.labelElement = document.querySelector(".session-label");
        this.endButton = document.getElementById("end-session-button");
        this.timerInterval = null;
        this.startTime = null;
    }

    show(sessionId, intentTranscript) {
        // C: Show user intent instead of generic label
        if (intentTranscript) {
            this.labelElement.textContent = `Working on: ${intentTranscript}`;
        } else {
            this.labelElement.textContent = "Session running";
        }
        this.element.classList.remove("hidden");
        document.getElementById("home-view").classList.add("hidden");
        document.getElementById("listening-indicator").classList.add("hidden");
        this.startTime = Date.now();
        this._startTimer();
    }

    hide() {
        this.element.classList.add("hidden");
        document.getElementById("home-view").classList.remove("hidden");
        this._stopTimer();
    }

    showInterrupted() {
        this.labelElement.textContent = "Reconnecting...";
        this._stopTimer();
    }

    showResumed() {
        this.labelElement.textContent = "Session running";
        if (!this.timerInterval) this._startTimer();
    }

    _startTimer() {
        this._stopTimer();
        this.startTime = this.startTime || Date.now();
        this.timerInterval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.startTime) / 1000);
            const minutes = Math.floor(elapsed / 60)
                .toString()
                .padStart(2, "0");
            const seconds = (elapsed % 60).toString().padStart(2, "0");
            this.timerElement.textContent = `${minutes}:${seconds}`;
        }, 1000);
    }

    _stopTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
            this.timerInterval = null;
        }
    }
}
