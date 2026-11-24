// App shell: header, view switching, focus → SelectionContext, keys → action registry,
// server events and the Ui-protocol dialogs.
import * as rpc from './rpc.js';
import { h, clear, fill, debounce, prettyKey, splitter, store, svg } from './dom.js';
import {
  toast, confirmDialog, promptDialog, formDialog, chooseDialog, errorDialog, outputDialog,
  pickList, contextMenu, isModalOpen, isMenuOpen, closeMenu,
} from './overlays.js';
import { LogView } from './log.js';
import { ChangesView } from './changes.js';
import { Sidebar } from './sidebar.js';

const $ = (id) => document.getElementById(id);
const isMac = /Mac|iPhone|iPad/.test(navigator.platform);

const app = {
  view: 'log',
  focusRegion: null,
  available: [],
  availableKeys: new Set(),
  state: null,

  current() { return this.view === 'log' ? this.log : this.changes; },

  ctx() {
    if (this.focusRegion === 'branches') return this.sidebar.ctx();
    return this.current().ctx();
  },

  setFocusRegion(region) {
    this.focusRegion = region;
    document.querySelectorAll('.focus-region.focused').forEach((el) => el.classList.remove('focused'));
  },

  showView(name) {
    if (name !== 'log' && name !== 'changes') return;
    const changed = this.view !== name;
    this.view = name;
    store('view', name);
    $('view-log').hidden = name !== 'log';
    $('view-changes').hidden = name !== 'changes';
    document.querySelectorAll('#tabs .tab').forEach((t) => t.classList.toggle('active', t.dataset.view === name));
    const v = this.current();
    if (v.stale) v.reload();
    if (changed) {
      v.focusDefault();
      this.focusRegion = v.focus;
    }
    this.contextChanged();
  },

  async run(id, ctx) {
    closeMenu();
    try {
      await rpc.call('actions.run', { id, ctx: ctx ?? this.ctx() });
    } catch (e) {
      toast(e.message, 'error');
    }
  },

  async menuAt(x, y, ctx) {
    let actions;
    try {
      actions = await rpc.call('actions.available', { ctx });
    } catch (e) {
      toast(e.message, 'error');
      return;
    }
    if (!actions.length) { toast('No actions for this selection'); return; }
    contextMenu(x, y, actions, (a) => this.run(a.id, ctx));
  },

  setChangesCount(n) { $('changes-count').textContent = n ? String(n) : ''; },
};
app.contextChanged = debounce(() => refreshHints(), 120);

// -- header / state ---------------------------------------------------------------------
async function refreshState() {
  let s;
  try {
    s = await rpc.call('repo.state');
  } catch (e) {
    toast(e.message, 'error');
    return;
  }
  app.state = s;
  $('repo-chip').textContent = s.repo;
  $('repo-chip').title = s.path;
  document.title = `${s.repo} · ${s.branch || 'detached'} — gitpanel`;
  const chip = clear($('branch-chip'));
  chip.append(
    svg('svg', { viewBox: '0 0 16 16', 'stroke-linecap': 'round' },
      svg('path', { d: 'M5 3v10M11 5a2 2 0 1 0 0-.01M11 7c0 3-6 2-6 6' })),
    h('span', { class: 'name' }, s.branch || `detached @ ${(s.head || '').slice(0, 8)}`));
  if (s.upstream) {
    chip.append(h('span', { class: 'ab', title: `${s.upstream}: ${s.ahead} ahead, ${s.behind} behind` },
      s.ahead ? h('span', { class: 'up' }, `↑${s.ahead} `) : null, s.behind ? h('span', { class: 'down' }, `↓${s.behind}`) : null,
      !s.ahead && !s.behind ? '✓' : null));
  } else if (s.branch) chip.append(h('span', { class: 'ab', title: 'No upstream' }, '⊘'));
  if (s.worktree) chip.title = `Worktree: ${s.worktree}`;
  app.setChangesCount(s.changes);
  const undo = $('undo-btn');
  const redo = $('redo-btn');
  undo.disabled = !s.undo;
  undo.title = s.undo ? `Undo: ${s.undo}  (z)` : 'Nothing to undo';
  redo.disabled = !s.redo;
  redo.title = s.redo ? `Redo: ${s.redo}  (Z)` : 'Nothing to redo';
  const banner = $('op-banner');
  banner.hidden = !s.operation;
  if (s.operation) {
    const avail = await rpc.call('actions.available', { ctx: { screen: app.view === 'log' ? 'log' : 'commit' }, palette: true }).catch(() => []);
    fill(banner, h('span', {}, `⚠ ${s.operation.label}`),
      s.operation.conflicts ? h('span', {}, `· ${s.operation.conflicts} conflict${s.operation.conflicts === 1 ? '' : 's'}`) : null,
      ...avail.filter((a) => a.id.startsWith('op.')).map((a) => h('button', { onclick: () => app.run(a.id, { screen: 'commit' }) }, a.label)));
  }
}

async function refreshHints() {
  let avail;
  try {
    avail = await rpc.call('actions.available', { ctx: app.ctx(), palette: true });
  } catch {
    return;
  }
  app.available = avail;
  app.availableKeys = new Set(avail.flatMap((a) => a.keys));
  const shown = avail.filter((a) => a.keys.length && !a.id.startsWith('app.show_') && a.id !== 'app.quit');
  shown.sort((a, b) => b.scoped - a.scoped);
  const local = { log: [['/', 'search'], ['⇧↑↓', 'select']], changes: [] }[app.view] || [];
  fill($('hints'), 
    ...shown.slice(0, 9).map((a) => h('span', {}, h('kbd', {}, prettyKey(a.keys[0])), a.label.replace(/…$/, ''))),
    ...local.map(([k, l]) => h('span', {}, h('kbd', {}, k), l)),
    h('span', {}, h('kbd', {}, isMac ? '⌘P' : '⌃P'), 'all actions'));
}

const refreshAll = (() => {
  let pending = new Set();
  const run = debounce(async () => {
    const kinds = pending;
    pending = new Set();
    const any = (...ks) => !kinds.size || ks.some((k) => kinds.has(k));
    refreshState();
    if (any('REFS', 'HEAD', 'WORKTREES', 'CONFIG')) app.sidebar.reload();
    const logNeeds = any('HEAD', 'REFS', 'STASH', 'OPERATION', 'WORKTREES');
    const changesNeed = any('INDEX', 'WORKTREE', 'HEAD', 'OPERATION', 'WORKTREES');
    if (logNeeds) { if (app.view === 'log') app.log.onChanged(); else app.log.stale = true; }
    if (changesNeed) { if (app.view === 'changes') app.changes.onChanged(); else app.changes.stale = true; }
    app.contextChanged();
  }, 120);
  return (kinds) => { (kinds || []).forEach((k) => pending.add(k)); if (!kinds) pending.clear(); run(); };
})();

// -- palette and branch picker ----------------------------------------------------------------
async function openPalette() {
  const ctx = app.ctx();
  const avail = await rpc.call('actions.available', { ctx, palette: true }).catch(() => []);
  const items = avail.map((a) => ({ value: a.id, label: a.label, group: a.group, key: a.keys[0], danger: a.danger }));
  items.sort((a, b) => (a.group || '').localeCompare(b.group || ''));
  const id = await pickList({ title: 'Actions', items, grouped: true, placeholder: 'Run an action…' });
  if (id) app.run(id, ctx);
}

async function openBranchPicker() {
  const data = app.sidebar.data;
  const items = data.local.map((b) => ({ value: b.name, label: b.short, desc: b.current ? 'current' : (b.upstream || ''), group: 'Local' }))
    .concat(data.remote.map((b) => ({ value: b.name, label: b.short, desc: 'remote', group: 'Remote' })));
  const name = await pickList({ title: 'Checkout branch', items, grouped: true, placeholder: 'Find a branch…' });
  if (name) app.run('branch.checkout', { screen: 'log', focus: 'branch', refs: [name] });
}

// -- server events -------------------------------------------------------------------------
const outputs = new Map();

rpc.onAsk(async (msg) => {
  const reply = (v) => rpc.answer(msg.id, v);
  switch (msg.kind) {
    case 'confirm': reply(await confirmDialog(msg)); break;
    case 'prompt': reply(await promptDialog(msg)); break;
    case 'form': reply(await formDialog(msg)); break;
    case 'choose': reply(await chooseDialog(msg)); break;
    case 'output': {
      let code = 0;
      const dlg = outputDialog(msg.title, () => { outputs.delete(msg.id); rpc.answer(msg.id, code); });
      outputs.set(msg.id, { dlg, setCode: (c) => { code = c; } });
      break;
    }
    default: reply(null);
  }
});
rpc.on('output', (m) => outputs.get(m.id)?.dlg.line(m.line));
rpc.on('output_done', (m) => { const o = outputs.get(m.id); if (o) { o.setCode(m.code); o.dlg.done(m.code); } });
rpc.on('notify', (m) => toast(m.message, m.severity));
rpc.on('error', (m) => errorDialog(m));
rpc.on('changed', (m) => refreshAll(m.kinds));
rpc.on('hello', (m) => { for (const e of m.errors || []) toast(`config: ${e}`, 'warning', 8000); });

rpc.on('open_screen', ({ name, args }) => {
  if (name === 'log') {
    app.showView('log');
    if (args?.branch) { app.sidebar.marked = new Set([String(args.branch)]); app.sidebar.apply(); }
    if (args?.sha) app.log.selectSha(String(args.sha));
  } else if (name === 'commit') app.showView('changes');
  else if (name === 'branches') openBranchPicker();
  else toast(`“${name}” isn't in the web prototype yet — it's in the terminal UI.`, 'warning');
});

rpc.on('post', ({ event, data }) => {
  switch (event) {
    case 'commit.done':
    case 'commit.toggle_amend':
      app.changes.onPost(event);
      break;
    case 'clipboard':
      navigator.clipboard?.writeText(String(data.text || '')).then(
        () => toast(`Copied ${String(data.text).slice(0, 40)}`), () => toast('Clipboard not available', 'warning'));
      break;
    case 'open_url':
      if (/^https?:\/\//.test(data.url || '')) window.open(data.url, '_blank', 'noopener');
      break;
    case 'context_menu': {
      const anchor = document.querySelector('.crow.cursor, .frow.selected, .side-item.cursor') || document.activeElement;
      const r = anchor?.getBoundingClientRect?.() || { left: innerWidth / 2, bottom: innerHeight / 3 };
      app.menuAt(r.left + 40, r.bottom, app.ctx());
      break;
    }
    case 'app.quit':
      toast('Close this tab to leave — stop the server with ctrl+c in its terminal.');
      break;
    case 'branches.refresh':
    case 'popup.close':
      app.sidebar.reload();
      break;
    case 'journal.refresh':
      refreshState();
      break;
    default:
      break;
  }
});

rpc.on('open', () => {
  const c = $('conn');
  c.className = 'conn ok';
  c.querySelector('.conn-text').textContent = 'live';
  refreshAll(null);
});
rpc.on('close', () => {
  const c = $('conn');
  c.className = 'conn bad';
  c.querySelector('.conn-text').textContent = 'reconnecting…';
});

// -- keys --------------------------------------------------------------------------------
const NAMED = {
  Enter: 'enter', ' ': 'space', Escape: 'escape', Tab: 'tab', ArrowUp: 'up', ArrowDown: 'down', ArrowLeft: 'left',
  ArrowRight: 'right', Backspace: 'backspace', Delete: 'delete', Home: 'home', End: 'end', PageUp: 'pageup',
  PageDown: 'pagedown', '?': 'question_mark', '.': 'full_stop', '[': 'left_square_bracket', ']': 'right_square_bracket', '/': 'slash',
};

// KeyboardEvent → Textual key syntax with modifiers sorted (matches api.norm_key)
function textualKey(e) {
  const ctrl = e.ctrlKey || (isMac && e.metaKey);
  let base;
  if (NAMED[e.key]) base = NAMED[e.key];
  else if (/^F\d{1,2}$/.test(e.key)) base = e.key.toLowerCase();
  else if ((e.altKey || ctrl) && /^Key[A-Z]$/.test(e.code)) base = e.shiftKey ? e.code[3] : e.code[3].toLowerCase();
  else if ((e.altKey || ctrl) && /^Digit\d$/.test(e.code)) base = e.code[5];
  else if (e.key.length === 1) base = e.key;
  else return null;
  const mods = [];
  if (e.altKey) mods.push('alt');
  if (ctrl) mods.push('ctrl');
  const named = base.length > 1;
  if (e.shiftKey && (named || ((e.altKey || ctrl) && !/[A-Z]/.test(base)))) mods.push('shift');
  return [...mods.sort(), base].join('+');
}

document.addEventListener('keydown', (e) => {
  if (isModalOpen() || isMenuOpen() || e.isComposing) return;
  const typing = e.target.matches?.('input, textarea, select, [contenteditable]');
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'p' || e.key === 'P')) {
    e.preventDefault();
    openPalette();
    return;
  }
  if (typing) {
    if (e.key === 'Escape') { e.target.blur(); app.current().focusDefault(); return; }
    if (!(e.ctrlKey || e.altKey || e.metaKey)) return;
  } else if (app.current().handleKey(e)) {
    e.preventDefault();
    return;
  }
  const key = textualKey(e);
  if (!key || !app.availableKeys.has(key)) return;
  if (key === 'tab' || key === 'shift+tab') return; // keep focus navigation
  e.preventDefault();
  e.stopPropagation();
  rpc.call('actions.key', { key, ctx: app.ctx() }).catch((err) => toast(err.message, 'error'));
});

// -- boot ----------------------------------------------------------------------------------
app.sidebar = new Sidebar(app);
app.log = new LogView(app, $('view-log'));
app.changes = new ChangesView(app, $('view-changes'));

document.querySelectorAll('#tabs .tab').forEach((t) => t.addEventListener('click', () => app.showView(t.dataset.view)));
document.querySelectorAll('[data-action]').forEach((b) => b.addEventListener('click', () => app.run(b.dataset.action)));
$('palette-btn').addEventListener('click', openPalette);
$('branch-chip').addEventListener('click', openBranchPicker);
document.addEventListener('focusin', (e) => {
  if (e.target.closest?.('#sidebar')) {
    if (e.target.id === 'branch-filter') return;
    app.setFocusRegion('branches');
  }
});

const body = document.querySelector('.body');
const sw = store('sidebar.w');
if (sw) document.documentElement.style.setProperty('--sidebar-w', `${sw}px`);
splitter(document.querySelector('.splitter-x[data-split="sidebar"]'), {
  axis: 'x', get: () => $('sidebar').getBoundingClientRect().width, min: 160, max: () => body.clientWidth / 2,
  set: (v) => { document.documentElement.style.setProperty('--sidebar-w', `${v}px`); store('sidebar.w', v); },
});

let blurred = false;
window.addEventListener('blur', () => { blurred = true; });
window.addEventListener('focus', () => { if (blurred) rpc.call('repo.focus').catch(() => {}); blurred = false; });

// the socket's "open" refreshes everything; don't load twice on boot
app.log.stale = false;
app.changes.stale = false;
app.showView(store('view') || 'log');
rpc.connect();
