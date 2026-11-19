// Toasts, modal dialogs (the Ui protocol's confirm/prompt/form/choose/output/error),
// the command palette and context menus.
import { h, clear, fill, prettyKey } from './dom.js';

const layer = () => document.getElementById('layer');
let openCount = 0;
export const isModalOpen = () => openCount > 0;

// -- toasts ------------------------------------------------------------------------
export function toast(message, severity = 'information', ms) {
  const box = document.getElementById('toasts');
  const el = h('div', { class: `toast ${severity}` },
    h('span', { class: 'bar' }), h('div', {}, message),
    h('button', { class: 'x', onclick: () => close(), title: 'Dismiss' }, '✕'));
  const close = () => { el.classList.add('leaving'); setTimeout(() => el.remove(), 200); };
  box.append(el);
  while (box.children.length > 5) box.firstChild.remove();
  setTimeout(close, ms ?? (severity === 'error' ? 9000 : severity === 'warning' ? 6000 : 3500));
}

// -- modal base -------------------------------------------------------------------------
function modal(build, { onCancel, wide = false, cls = '' } = {}) {
  const prevFocus = document.activeElement;
  const dlg = h('div', { class: `dialog ${wide ? 'wide' : ''} ${cls}`, role: 'dialog', 'aria-modal': 'true' });
  const back = h('div', { class: 'backdrop' }, dlg);
  let done = false;
  const close = () => {
    if (done) return;
    done = true;
    openCount--;
    back.remove();
    document.removeEventListener('keydown', onKey, true);
    if (prevFocus && prevFocus.focus) prevFocus.focus();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel?.(); close(); }
  };
  back.addEventListener('mousedown', (e) => { if (e.target === back) { onCancel?.(); close(); } });
  document.addEventListener('keydown', onKey, true);
  openCount++;
  build(dlg, close);
  layer().append(back);
  const first = dlg.querySelector('[autofocus], input, textarea, select, .btn.primary, .btn.danger, .btn');
  first?.focus();
  return close;
}

function footer(...buttons) { return h('div', { class: 'dfoot' }, ...buttons); }

export function confirmDialog({ title, body, danger }) {
  return new Promise((resolve) => {
    modal((d, close) => {
      const ok = h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => { close(); resolve(true); } }, danger ? 'Yes, do it' : 'OK');
      fill(d, h('h3', {}, title), body ? h('div', { class: 'dbody' }, body) : null,
        footer(h('button', { class: 'btn', onclick: () => { close(); resolve(false); } }, 'Cancel'), ok));
      setTimeout(() => ok.focus());
      d.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ok.click(); } });
    }, { onCancel: () => resolve(false) });
  });
}

export function promptDialog({ title, default: def = '', placeholder = '', multiline = false }) {
  return new Promise((resolve) => {
    modal((d, close) => {
      const input = multiline
        ? h('textarea', { spellcheck: 'false', placeholder })
        : h('input', { type: 'text', spellcheck: 'false', placeholder });
      input.value = def;
      const ok = () => { close(); resolve(input.value); };
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && (!multiline || e.ctrlKey || e.metaKey)) { e.preventDefault(); ok(); }
      });
      fill(d, h('h3', {}, title), h('div', { class: 'dfields' }, input),
        footer(h('button', { class: 'btn', onclick: () => { close(); resolve(null); } }, 'Cancel'),
          h('button', { class: 'btn primary', onclick: ok }, 'OK', multiline ? h('kbd', {}, '⌃⏎') : null)));
      setTimeout(() => { input.focus(); input.select?.(); });
    }, { onCancel: () => resolve(null), wide: multiline });
  });
}

export function formDialog({ title, fields, body, ok_label = 'OK', danger }) {
  return new Promise((resolve) => {
    modal((d, close) => {
      const inputs = {};
      const rows = fields.map((f) => {
        if (f.kind === 'bool') {
          const cb = h('input', { type: 'checkbox' });
          cb.checked = !!f.default;
          inputs[f.name] = () => cb.checked;
          return h('label', { class: 'check' }, cb, f.label);
        }
        if (f.kind === 'choice') {
          const sel = h('select', {}, ...f.options.map(([v, l]) => h('option', { value: v }, l)));
          sel.value = f.default ?? (f.options[0] || [''])[0];
          inputs[f.name] = () => sel.value;
          return h('label', { class: 'lbl' }, f.label, sel);
        }
        const inp = h('input', { type: 'text', spellcheck: 'false', placeholder: f.placeholder || '' });
        inp.value = f.default ?? '';
        inputs[f.name] = () => inp.value;
        return h('label', { class: 'lbl' }, f.label, inp);
      });
      const ok = () => {
        close();
        resolve(Object.fromEntries(Object.entries(inputs).map(([k, get]) => [k, get()])));
      };
      fill(d, h('h3', {}, title), body ? h('div', { class: 'dbody' }, body) : null, h('div', { class: 'dfields' }, rows),
        footer(h('button', { class: 'btn', onclick: () => { close(); resolve(null); } }, 'Cancel'),
          h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: ok }, ok_label)));
      d.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); ok(); } });
    }, { onCancel: () => resolve(null) });
  });
}

export function errorDialog({ title, details }) {
  modal((d, close) => {
    fill(d, h('h3', {}, title), h('pre', { class: 'out' }, details || ''),
      footer(h('button', { class: 'btn primary', onclick: close }, 'Close')));
  }, { wide: true, cls: 'error' });
}

// Streams output lines until `done(code)`, then waits for Close.
export function outputDialog(title, onClosed) {
  let pre, btn;
  const close = modal((d, closeFn) => {
    pre = h('pre', { class: 'out' });
    btn = h('button', { class: 'btn primary', disabled: true, onclick: () => { closeFn(); onClosed(); } }, 'Running…');
    fill(d, h('h3', { class: 'mono' }, title), pre, footer(btn));
  }, { wide: true, onCancel: () => { if (btn.disabled) return; onClosed(); } });
  return {
    line(text) {
      const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
      pre.append(text + '\n');
      if (atEnd) pre.scrollTop = pre.scrollHeight;
    },
    done(code) {
      btn.disabled = false;
      btn.textContent = code ? `Close (exit ${code})` : 'Close';
      if (code) btn.classList.replace('primary', 'danger');
      btn.focus();
    },
    close,
  };
}

// -- filterable list (choose dialog + command palette) -----------------------------------
function highlight(label, q) {
  if (!q) return label;
  const i = label.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return label;
  return [label.slice(0, i), h('mark', {}, label.slice(i, i + q.length)), label.slice(i + q.length)];
}

function score(item, q) {
  if (!q) return 1;
  const l = item.label.toLowerCase();
  const s = q.toLowerCase();
  if (l.startsWith(s)) return 3;
  if (l.includes(s)) return 2;
  // all words present
  return s.split(/\s+/).every((w) => l.includes(w) || (item.group || '').toLowerCase().includes(w)) ? 1 : 0;
}

export function pickList({ title, items, placeholder = 'Type to filter…', grouped = false }) {
  return new Promise((resolve) => {
    modal((d, close) => {
      d.classList.add('palette');
      const q = h('input', { class: 'q', placeholder: title ? `${title} — ${placeholder}` : placeholder, spellcheck: 'false' });
      const list = h('div', { class: 'plist' });
      let shown = [];
      let active = 0;
      const pick = (it) => { close(); resolve(it ? it.value : null); };
      const render = () => {
        const query = q.value.trim();
        shown = items.map((it) => [score(it, query), it]).filter(([s]) => s > 0)
          .sort((a, b) => b[0] - a[0]).map(([, it]) => it);
        if (!query && grouped) shown = items;
        active = Math.min(active, Math.max(0, shown.length - 1));
        clear(list);
        if (!shown.length) { list.append(h('div', { class: 'pempty' }, 'No matches')); return; }
        let group = null;
        shown.forEach((it, i) => {
          if (grouped && !query && it.group !== group) {
            group = it.group;
            list.append(h('div', { class: 'pgroup' }, group || 'Other'));
          }
          const row = h('div', {
            class: `pitem ${i === active ? 'active' : ''} ${it.danger ? 'danger' : ''}`,
            onmousemove: () => { if (active !== i) { active = i; mark(); } },
            onclick: () => pick(it),
          }, h('span', { class: 'lab' }, highlight(it.label, query)),
          it.desc ? h('span', { class: 'desc' }, it.desc) : null,
          it.key ? h('kbd', {}, prettyKey(it.key)) : null);
          list.append(row);
        });
      };
      const mark = () => {
        list.querySelectorAll('.pitem').forEach((el, i) => el.classList.toggle('active', i === active));
        list.querySelectorAll('.pitem')[active]?.scrollIntoView({ block: 'nearest' });
      };
      q.addEventListener('input', () => { active = 0; render(); });
      q.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); mark(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); mark(); }
        else if (e.key === 'Enter') { e.preventDefault(); pick(shown[active]); }
      });
      fill(d, q, list);
      render();
      setTimeout(() => q.focus());
    }, { onCancel: () => resolve(null) });
  });
}

export function chooseDialog({ title, options }) {
  return pickList({ title, items: options.map(([value, label]) => ({ value, label })) });
}

// -- context menu ---------------------------------------------------------------------
let menuEl = null;
export function closeMenu() { menuEl?.remove(); menuEl = null; }

export function contextMenu(x, y, actions, run) {
  closeMenu();
  if (!actions.length) return;
  const m = h('div', { class: 'menu', role: 'menu' });
  let group = null;
  const items = [];
  // one section per group, in order of first appearance (registration order interleaves them)
  const groups = new Map();
  for (const a of actions) {
    if (!groups.has(a.group)) groups.set(a.group, []);
    groups.get(a.group).push(a);
  }
  for (const a of [...groups.values()].flat()) {
    if (a.group !== group) {
      if (group !== null) m.append(h('div', { class: 'menu-sep' }));
      group = a.group;
      m.append(h('div', { class: 'pgroup' }, group || 'Other'));
    }
    const it = h('div', {
      class: `pitem ${a.danger ? 'danger' : ''}`,
      onclick: () => { closeMenu(); run(a); },
      onmousemove: () => setActive(items.indexOf(it)),
    }, h('span', { class: 'lab' }, a.label), a.keys?.length ? h('kbd', {}, prettyKey(a.keys[0])) : null);
    items.push(it);
    m.append(it);
  }
  let active = -1;
  const setActive = (i) => { active = i; items.forEach((el, j) => el.classList.toggle('active', j === i)); items[i]?.scrollIntoView({ block: 'nearest' }); };
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`;
  m.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
  menuEl = m;
  const onKey = (e) => {
    if (!menuEl) { document.removeEventListener('keydown', onKey, true); return; }
    if (e.key === 'Escape') { closeMenu(); }
    else if (e.key === 'ArrowDown') setActive(Math.min(active + 1, items.length - 1));
    else if (e.key === 'ArrowUp') setActive(Math.max(active - 1, 0));
    else if (e.key === 'Enter' && active >= 0) items[active].click();
    else return;
    e.preventDefault(); e.stopPropagation();
  };
  document.addEventListener('keydown', onKey, true);
  setTimeout(() => document.addEventListener('mousedown', function off(e) {
    if (menuEl && !menuEl.contains(e.target)) closeMenu();
    if (!menuEl) document.removeEventListener('mousedown', off);
  }));
}
export const isMenuOpen = () => !!menuEl;
