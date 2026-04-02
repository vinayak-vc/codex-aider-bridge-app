// core/sounds.js — Synthetic sound effects via Web Audio API (no files needed)

let _ctx = null;

function ctx() {
  if (!_ctx) {
    try { _ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch (_) {}
  }
  return _ctx;
}

function resume() {
  // AudioContext needs user gesture to start; resume silently
  if (_ctx && _ctx.state === 'suspended') _ctx.resume().catch(() => {});
}

// ── Low-level tone builder ────────────────────────────────────────────────────

function tone(freq, startTime, duration, gain = 0.25, type = 'sine', fadeIn = 0.005) {
  const c = ctx();
  if (!c) return;
  const osc = c.createOscillator();
  const g   = c.createGain();
  osc.connect(g);
  g.connect(c.destination);
  osc.type = type;
  osc.frequency.setValueAtTime(freq, startTime);
  g.gain.setValueAtTime(0, startTime);
  g.gain.linearRampToValueAtTime(gain, startTime + fadeIn);
  g.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  osc.start(startTime);
  osc.stop(startTime + duration + 0.01);
}

function noise(startTime, duration, gain = 0.08) {
  const c = ctx();
  if (!c) return;
  const buf  = c.createBuffer(1, c.sampleRate * duration, c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  const src = c.createBufferSource();
  src.buffer = buf;
  const g = c.createGain();
  src.connect(g); g.connect(c.destination);
  g.gain.setValueAtTime(gain, startTime);
  g.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);
  src.start(startTime); src.stop(startTime + duration);
}

// ── Named sound effects ───────────────────────────────────────────────────────

const SOUNDS = {
  /** Launch button — ascending 3-note arpeggio */
  launch() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(330, t,       0.12, 0.18, 'triangle');
    tone(415, t + 0.1, 0.12, 0.18, 'triangle');
    tone(523, t + 0.2, 0.22, 0.22, 'triangle');
  },

  /** Success / run complete — warm major chord swell */
  success() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(523, t,       0.5, 0.15, 'sine');   // C5
    tone(659, t + 0.05, 0.45, 0.12, 'sine'); // E5
    tone(784, t + 0.1, 0.4,  0.10, 'sine');  // G5
    tone(1046,t + 0.15, 0.55, 0.08, 'sine'); // C6
  },

  /** Error — low descending buzz */
  error() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(220, t,       0.18, 0.22, 'sawtooth');
    tone(185, t + 0.15, 0.25, 0.20, 'sawtooth');
    noise(t, 0.35, 0.05);
  },

  /** Task approved — single soft ding */
  approved() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(880, t, 0.35, 0.18, 'sine');
    tone(1108, t + 0.02, 0.28, 0.10, 'sine');
  },

  /** Task rework — two short attention beeps */
  rework() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(440, t,       0.1, 0.18, 'square');
    tone(440, t + 0.18, 0.1, 0.18, 'square');
  },

  /** Task failed — descending minor */
  failed() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(311, t,       0.2, 0.2, 'triangle');
    tone(277, t + 0.18, 0.3, 0.18, 'triangle');
  },

  /** Review required — attention chime (two-tone alert) */
  reviewRequired() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(587, t,        0.15, 0.2, 'sine');
    tone(740, t + 0.12, 0.2,  0.2, 'sine');
    tone(587, t + 0.32, 0.15, 0.15,'sine');
  },

  /** Message sent in chat — short keyboard click */
  messageSent() {
    resume();
    noise(ctx()?.currentTime || 0, 0.04, 0.12);
  },

  /** Chat response complete — soft pop */
  chatDone() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(660, t, 0.18, 0.14, 'sine');
  },

  /** Bridge started — startup whoosh */
  bridgeStarted() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    const osc = c.createOscillator();
    const g   = c.createGain();
    osc.connect(g); g.connect(c.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(150, t);
    osc.frequency.exponentialRampToValueAtTime(600, t + 0.4);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(0.18, t + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
    osc.start(t); osc.stop(t + 0.5);
  },

  /** Run stopped by user — descending sweep */
  stopped() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    const osc = c.createOscillator();
    const g   = c.createGain();
    osc.connect(g); g.connect(c.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(500, t);
    osc.frequency.exponentialRampToValueAtTime(120, t + 0.35);
    g.gain.setValueAtTime(0.16, t);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.4);
    osc.start(t); osc.stop(t + 0.45);
  },

  /** Input/stdin sent — tick */
  inputSent() {
    resume();
    const c = ctx(); if (!c) return;
    tone(1200, c.currentTime, 0.05, 0.1, 'square');
  },

  /** Relay decision submitted — confirm tone */
  relayDecision() {
    resume();
    const c = ctx(); if (!c) return;
    const t = c.currentTime;
    tone(440, t,      0.1, 0.15, 'sine');
    tone(554, t + 0.1, 0.15, 0.15, 'sine');
  },
};

// ── User preference (mutable) ─────────────────────────────────────────────────

let _muted = localStorage.getItem('bridge-sounds-muted') === 'true';

export function isMuted() { return _muted; }

export function setMuted(v) {
  _muted = Boolean(v);
  localStorage.setItem('bridge-sounds-muted', _muted);
}

export function toggleMute() { setMuted(!_muted); }

/** Play a named sound. Silently ignored if muted or AudioContext unavailable. */
export function play(name) {
  if (_muted) return;
  try { SOUNDS[name]?.(); } catch (_) {}
}
