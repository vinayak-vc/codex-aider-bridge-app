// store.js — lightweight reactive in-memory state store

const _state = {
  runStatus: 'idle',   // idle | running | success | failure | stopped
  isPaused: false,
  tasks: {},           // id → task object
  totalTasks: 0,
  completedTasks: 0,
  repoRoot: '',
  driver: '',          // Claude | Codex | Cursor | Windsurf | Manual
  runId: null,
  reviewPending: null, // { task_id, validation_message } | null
};

const _subs = {}; // key → [fn, ...]

export const store = {
  get(key) {
    return _state[key];
  },

  set(key, value) {
    _state[key] = value;
    (_subs[key] || []).forEach(fn => { try { fn(value); } catch (_) {} });
    (_subs['*'] || []).forEach(fn => { try { fn(key, value); } catch (_) {} });
  },

  /** Subscribe to changes on a specific key (or '*' for any). */
  subscribe(key, fn) {
    if (!_subs[key]) _subs[key] = [];
    _subs[key].push(fn);
    return () => { _subs[key] = _subs[key].filter(f => f !== fn); };
  },

  /** Snapshot of all state (for debugging). */
  snapshot() { return { ..._state }; },
};
