// Tiny DOM + formatting helpers.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const SVG = 'http://www.w3.org/2000/svg';
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const c of children) if (c) el.append(c);
  return el;
}

export function icon(name) {
  const paths = {
    branch: 'M5 3v10M5 13a2 2 0 1 0 0 .01M11 5a2 2 0 1 0 0-.01M11 7c0 3-6 2-6 6',
    remote: 'M3 9a5 5 0 0 1 10 0M5 9a3 3 0 0 1 6 0M8 9v4',
    tag: 'M3 3h5l5 5-5 5-5-5zM6 6h.01',
    all: 'M3 4h10M3 8h10M3 12h10',
    star: 'M8 2.5l1.7 3.5 3.8.5-2.8 2.6.7 3.8L8 11l-3.4 1.9.7-3.8L2.5 6.5l3.8-.5z',
    folder: 'M2 4h4l1.5 1.5H14V12H2z',
  };
  return svg('svg', { class: 'ico', viewBox: '0 0 16 16', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' },
    svg('path', { d: paths[name] || paths.all }));
}

export function clear(el) { while (el.firstChild) el.firstChild.remove(); return el; }

// Replace an element's children (null/false children are skipped, unlike Element.append).
export function fill(el, ...children) { clear(el); append(el, children); return el; }

export function relDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 7) return `${Math.floor(s / 86400)} d ago`;
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
}

export function fullDate(iso) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';
}

const AVATAR = ['var(--green)', 'var(--teal)', 'var(--blue)', 'var(--pink)', 'var(--lilac)', 'var(--amber)'];
export function avatar(name) {
  let n = 0;
  for (const ch of name || '?') n = (n * 31 + ch.charCodeAt(0)) >>> 0;
  const initials = (name || '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  return h('span', { class: 'avatar', style: { background: AVATAR[n % AVATAR.length] } }, initials || '?');
}

export function splitPath(p) {
  const i = p.lastIndexOf('/');
  return i < 0 ? ['', p] : [p.slice(0, i), p.slice(i + 1)];
}

// Textual key names → what the hint bar shows
const PRETTY = {
  enter: '⏎', space: 'Space', escape: 'Esc', question_mark: '?', full_stop: '.', left_square_bracket: '[',
  right_square_bracket: ']', up: '↑', down: '↓', left: '←', right: '→', tab: 'Tab', slash: '/',
};
export function prettyKey(k) {
  return k.split('+').map((p) => {
    if (p === 'ctrl') return '⌃';
    if (p === 'alt') return '⌥';
    if (p === 'shift') return '⇧';
    return PRETTY[p] || (p.length === 1 ? p : p.toUpperCase());
  }).join('');
}

export function debounce(fn, ms) {
  let t = null;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// A drag handle that resizes something by updating a CSS length.
export function splitter(handle, { axis, get, set, min = 120, max = () => 4000 }) {
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('drag');
    const start = axis === 'x' ? e.clientX : e.clientY;
    const base = get();
    const move = (ev) => {
      const d = (axis === 'x' ? ev.clientX : ev.clientY) - start;
      set(Math.max(min, Math.min(max(), base + d)));
    };
    const up = () => {
      handle.classList.remove('drag');
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
  });
}

export function store(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(`gitpanel.${key}`));
    localStorage.setItem(`gitpanel.${key}`, JSON.stringify(value));
  } catch { /* private mode etc. */ }
  return undefined;
}
