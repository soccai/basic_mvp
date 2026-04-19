class WebSocketClient {
    constructor(url) {
        this.url = url;
        this.ws = null;
        this.listeners = {};
        this.reconnectDelay = 1000;
        this.maxReconnectDelay = 30000;
        this.connected = false;
    }

    connect() {
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
                    this._emit(msg.type, msg);
                } catch (e) {
                    console.error("WS parse error:", e);
                }
            }
        };

        this.ws.onclose = () => {
            this.connected = false;
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
        setTimeout(() => {
            this.connect();
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 2,
                this.maxReconnectDelay
            );
        }, this.reconnectDelay);
    }
}
