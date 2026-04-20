class WebSocketClient {
    constructor(url) {
        this.baseUrl = url;
        this.url = url;
        this.ws = null;
        this.listeners = {};
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.connected = false;
        this.rejected = false;
    }

    connect() {
        // Append stored connection token for reconnect identification
        const token = sessionStorage.getItem("lifeos_connection_token");
        this.url = token
            ? `${this.baseUrl}?token=${encodeURIComponent(token)}`
            : this.baseUrl;

        this.ws = new WebSocket(this.url);
        this.ws.binaryType = "arraybuffer";

        this.ws.onopen = () => {
            this.connected = true;
            this.reconnectDelay = 1000;
            this._emit("connected");
        };

        this.ws.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                this._emit("audio", event.data);
            } else {
                try {
                    const msg = JSON.parse(event.data);

                    if (msg.type === "connection_accepted") {
                        sessionStorage.setItem("lifeos_connection_token", msg.connection_token);
                        this.rejected = false;
                    }

                    if (msg.type === "connection_rejected") {
                        this.rejected = true;
                    }

                    this._emit(msg.type, msg);
                } catch (e) {
                    console.error("WS parse error:", e);
                }
            }
        };

        this.ws.onclose = (event) => {
            this.connected = false;
            // Close code 4409 = rejected by connection gate (another tab active)
            if (event.code === 4409) {
                this.rejected = true;
            }
            this._emit("disconnected");
            this._reconnect();
        };

        this.ws.onerror = (err) => {
            this._emit("error", err);
        };
    }

    sendAudio(pcmBuffer) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(pcmBuffer);
        }
    }

    sendJSON(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(data));
        }
    }

    on(event, callback) {
        if (!this.listeners[event]) this.listeners[event] = [];
        this.listeners[event].push(callback);
    }

    _emit(event, data) {
        (this.listeners[event] || []).forEach((cb) => cb(data));
    }

    _reconnect() {
        if (this.rejected) return;
        setTimeout(() => {
            this.connect();
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 2,
                this.maxReconnectDelay
            );
        }, this.reconnectDelay);
    }
}
