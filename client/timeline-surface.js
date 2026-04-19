class TimelineSurface {
    constructor() {
        this.element = document.getElementById("timeline-surface");
        this.listElement = document.getElementById("timeline-list");
        this.closeButton = document.getElementById("timeline-close");
    }

    async fetchEntries() {
        try {
            const response = await fetch("/api/timeline");
            return await response.json();
        } catch (e) {
            console.error("Failed to fetch timeline:", e);
            return [];
        }
    }

    async fetchSession(sessionId) {
        try {
            const response = await fetch(`/api/sessions/${sessionId}`);
            if (!response.ok) return null;
            return await response.json();
        } catch (e) {
            console.error("Failed to fetch session:", e);
            return null;
        }
    }

    async show() {
        const entries = await this.fetchEntries();
        this._render(entries);
        this.element.classList.remove("hidden");
        document.getElementById("home-view").classList.add("hidden");
        document.getElementById("listening-indicator").classList.add("hidden");
    }

    hide() {
        this.element.classList.add("hidden");
        document.getElementById("home-view").classList.remove("hidden");
    }

    _render(entries) {
        this.listElement.innerHTML = "";
        if (entries.length === 0) {
            this.listElement.innerHTML =
                '<p class="timeline-empty">No sessions yet. Tap the mic to start.</p>';
            return;
        }
        entries.forEach((entry) => {
            const div = document.createElement("div");
            div.className = "timeline-entry";
            if (entry.session_id) {
                div.classList.add("clickable");
            }
            const time = new Date(entry.timestamp).toLocaleTimeString([], {
                hour: "numeric",
                minute: "2-digit",
            });
            div.innerHTML = `<span class="entry-text">${this._escapeHtml(entry.text)}</span>
                             <span class="entry-time">${time}</span>`;

            if (entry.session_id) {
                div.addEventListener("click", () => {
                    this._showSessionDetail(entry.session_id);
                });
            }

            this.listElement.appendChild(div);
        });
    }

    async _showSessionDetail(sessionId) {
        const session = await this.fetchSession(sessionId);
        if (!session) return;

        this.listElement.innerHTML = "";

        const detail = document.createElement("div");
        detail.className = "session-detail";

        const startedAt = session.started_at
            ? new Date(session.started_at).toLocaleString()
            : "—";
        const durationSec = session.duration_ms
            ? Math.floor(session.duration_ms / 1000)
            : 0;
        const minutes = Math.floor(durationSec / 60);
        const seconds = durationSec % 60;
        const duration = minutes
            ? `${minutes}m ${seconds}s`
            : `${seconds}s`;

        const summaryHtml = session.summary
            ? this._renderSummary(session.summary)
            : '<p class="summary-empty">No summary available.</p>';

        detail.innerHTML = `
            <button class="detail-back">\u2190 Back</button>
            <div class="detail-header">
                <span class="detail-status ${session.status}">${session.status}</span>
                <span class="detail-type">${this._escapeHtml(session.session_type)}</span>
                <span class="detail-duration">${duration}</span>
            </div>
            <div class="detail-meta">
                <p>${startedAt}</p>
            </div>
            <div class="detail-summary">${summaryHtml}</div>
        `;

        const backButton = detail.querySelector(".detail-back");
        backButton.addEventListener("click", () => {
            this.show();
        });

        this.listElement.appendChild(detail);
    }

    _renderSummary(raw) {
        // Strip markdown bold markers and split into paragraphs
        const cleaned = raw
            .replace(/\*\*(.*?)\*\*/g, "$1")
            .replace(/__(.*?)__/g, "$1");

        const lines = cleaned.split("\n").filter((l) => l.trim());
        let html = "";

        for (const line of lines) {
            const trimmed = line.trim();
            // Section heading (e.g., "Session Summary:", "Outcome:")
            if (
                trimmed.endsWith(":") &&
                trimmed.length < 60 &&
                !trimmed.startsWith("-")
            ) {
                html += `<h4 class="summary-heading">${this._escapeHtml(trimmed.replace(/:$/, ""))}</h4>`;
            } else if (trimmed.startsWith("- ") || trimmed.startsWith("• ")) {
                html += `<p class="summary-bullet">${this._escapeHtml(trimmed.slice(2))}</p>`;
            } else {
                html += `<p class="summary-text">${this._escapeHtml(trimmed)}</p>`;
            }
        }

        return html;
    }

    _escapeHtml(str) {
        const div = document.createElement("div");
        div.textContent = str;
        return div.innerHTML;
    }
}
