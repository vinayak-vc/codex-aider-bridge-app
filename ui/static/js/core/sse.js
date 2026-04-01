// sse.js — SSEClient with typed event dispatch and auto-reconnect

const RECONNECT_DELAY = 3000; // ms

export class SSEClient {
  constructor(url) {
    this._url = url;
    this._handlers = {}; // eventType → [fn, ...]
    this._es = null;
    this._stopped = false;
  }

  /** Register a handler for a specific event type (or '*' for all). */
  on(eventType, handler) {
    if (!this._handlers[eventType]) this._handlers[eventType] = [];
    this._handlers[eventType].push(handler);
    return this; // chainable
  }

  /** Start (or restart) the SSE connection. */
  connect() {
    this._stopped = false;
    this._open();
    return this;
  }

  /** Permanently stop — no reconnect. */
  disconnect() {
    this._stopped = true;
    if (this._es) { this._es.close(); this._es = null; }
  }

  // ── private ──────────────────────────────────────────────────────────────

  _open() {
    if (this._es) { this._es.close(); this._es = null; }
    const es = new EventSource(this._url);
    this._es = es;

    es.addEventListener('message', (e) => {
      try {
        const data = JSON.parse(e.data);
        const type = data.type || 'message';
        this._dispatch(type, data);
        this._dispatch('*', data);
      } catch (_) {}
    });

    es.addEventListener('error', () => {
      es.close();
      this._es = null;
      if (!this._stopped) {
        setTimeout(() => this._open(), RECONNECT_DELAY);
      }
    });
  }

  _dispatch(type, data) {
    const fns = this._handlers[type];
    if (fns) fns.forEach(fn => { try { fn(data); } catch (_) {} });
  }
}
