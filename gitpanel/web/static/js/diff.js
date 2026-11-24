// Unified diff view with per-hunk buttons and click/shift-click line selection.
// Selections are sent to the server as (hunk index, line index) pairs → patch.Selection.
import { h, clear, fill, splitPath } from './dom.js';

// Highlight the changed middle of a replaced line (common prefix/suffix stay plain).
function inline(a, b) {
  let p = 0;
  while (p < a.length && p < b.length && a[p] === b[p]) p++;
  let s = 0;
  while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s++;
  return [p, s];
}

function codeCell(text, kind, range) {
  if (!range) return h('td', { class: 'code' }, text);
  const [p, s] = range;
  const mid = text.slice(p, text.length - s);
  if (!mid || mid.length > text.length * 0.8) return h('td', { class: 'code' }, text);
  const tag = kind === '+' ? 'ins' : 'del';
  return h('td', { class: 'code' }, text.slice(0, p), h(tag, {}, mid), text.slice(text.length - s));
}

export class DiffView {
  constructor({ hunkActions = () => [], selectionActions = () => [], onAction = () => {}, onFocus = () => {} } = {}) {
    this.hunkActions = hunkActions;
    this.selectionActions = selectionActions;
    this.onAction = onAction;
    this.diff = null;
    this.source = null;
    this.picked = new Set(); // "h:l"
    this.anchor = null;
    this.title = h('div', { class: 'path' });
    this.headRight = h('div', { class: 'grow' });
    this.stat = h('div', { class: 'frow' });
    this.scroll = h('div', { class: 'diff-scroll' });
    this.selBar = h('div', { class: 'sel-bar', hidden: true });
    this.el = h('div', { class: 'diff focus-region', tabindex: '0', onfocus: () => onFocus() },
      h('div', { class: 'diff-head' }, this.title, this.headRight, this.stat),
      h('div', { class: 'diff-wrap' }, this.scroll, this.selBar));
    this.el.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.picked.size) { e.stopPropagation(); this.clearSelection(); }
    });
    this.empty('Select a file to see its changes');
  }

  empty(message) {
    this.diff = null;
    this.source = null;
    this.picked.clear();
    clear(this.title);
    clear(this.stat);
    fill(this.scroll, h('div', { class: 'empty' }, h('div', {}, message)));
    this.updateBar();
  }

  loading() {
    fill(this.scroll, h('div', { style: { padding: '16px', display: 'grid', gap: '10px' } },
      ...[70, 55, 82, 40].map((w) => h('div', { class: 'skeleton', style: { width: `${w}%` } }))));
  }

  set(diff, source, { keepSelection = false } = {}) {
    const samePlace = this.diff && diff && this.diff.path === diff.path && JSON.stringify(this.source) === JSON.stringify(source);
    const top = samePlace ? this.scroll.scrollTop : 0;
    this.diff = diff;
    this.source = source;
    if (!keepSelection || !samePlace) this.picked.clear();
    const [dir, name] = splitPath(diff.path);
    fill(this.title, dir ? h('span', { class: 'dir' }, `${dir}/`) : null, name,
      diff.old_path ? h('span', { class: 'dir' }, `  ← ${diff.old_path}`) : null);
    fill(this.stat, h('span', { class: 'stat' },
      h('span', { class: 'a' }, `+${diff.additions}`), ' ', h('span', { class: 'd' }, `−${diff.deletions}`)));
    this.render();
    this.scroll.scrollTop = top;
  }

  render() {
    const d = this.diff;
    const table = h('table', { class: `diff-table ${d.partial ? 'selectable' : ''}` });
    const body = h('tbody');
    table.append(body);
    if (d.binary) body.append(h('tr', { class: 'note' }, h('td', { colspan: 4, style: { padding: '16px' } }, 'Binary file')));
    if (d.mode) body.append(h('tr', { class: 'note' }, h('td', { colspan: 4, style: { padding: '8px 16px' } }, `mode ${d.mode}`)));
    if (!d.binary && !d.hunks.length) body.append(h('tr', { class: 'note' }, h('td', { colspan: 4, style: { padding: '16px' } }, 'No content changes')));
    d.hunks.forEach((hk, hi) => {
      const buttons = this.hunkActions(hi, d).map((a) => h('button', {
        class: `mini ${a.danger ? 'danger' : ''}`, title: a.title || a.label,
        onclick: (e) => { e.stopPropagation(); this.onAction(a.id, { hunks: [hi], lines: [] }); },
      }, a.label));
      body.append(h('tr', { class: 'hunk' }, h('td', { colspan: 4 },
        h('div', { class: 'hunk-bar' }, h('span', {}, (hk.header.match(/^@@[^@]*@@/) || [hk.header])[0]),
          hk.section ? h('span', { class: 'sec' }, hk.section) : null, ...buttons))));
      // pair up -/+ runs for intra-line highlights
      const ranges = new Map();
      for (let i = 0; i < hk.lines.length;) {
        if (hk.lines[i][0] !== '-') { i++; continue; }
        let j = i;
        while (j < hk.lines.length && hk.lines[j][0] === '-') j++;
        let k = j;
        while (k < hk.lines.length && hk.lines[k][0] === '+') k++;
        if (k - j === j - i && j - i <= 8) {
          for (let n = 0; n < j - i; n++) {
            const r = inline(hk.lines[i + n][1], hk.lines[j + n][1]);
            ranges.set(i + n, r);
            ranges.set(j + n, r);
          }
        }
        i = k;
      }
      hk.lines.forEach(([kind, text, oldNo, newNo], li) => {
        const cls = kind === '+' ? 'add' : kind === '-' ? 'del' : kind === '\\' ? 'note' : '';
        const key = `${hi}:${li}`;
        const tr = h('tr', { class: `${cls} ${this.picked.has(key) ? 'picked' : ''}` },
          h('td', { class: 'ln' }, oldNo ?? ''), h('td', { class: 'ln' }, newNo ?? ''),
          h('td', { class: 'mk' }, kind === ' ' ? '' : kind === '\\' ? '' : kind),
          codeCell(text, kind, ranges.get(li)));
        if (d.partial && (kind === '+' || kind === '-')) {
          tr.addEventListener('mousedown', (e) => { if (e.shiftKey) e.preventDefault(); });
          tr.addEventListener('click', (e) => this.toggle(hi, li, e.shiftKey));
        }
        body.append(tr);
      });
    });
    if (d.truncated) body.append(h('tr', { class: 'note' }, h('td', { colspan: 4, style: { padding: '12px 16px' } }, 'Diff truncated — the file is too large to show in full.')));
    fill(this.scroll, table);
    this.updateBar();
  }

  toggle(hi, li, range) {
    const key = `${hi}:${li}`;
    if (range && this.anchor) {
      const [ah, al] = this.anchor;
      const flat = [];
      this.diff.hunks.forEach((hk, h2) => hk.lines.forEach(([k], l2) => { if (k === '+' || k === '-') flat.push([h2, l2]); }));
      const ia = flat.findIndex(([a, b]) => a === ah && b === al);
      const ib = flat.findIndex(([a, b]) => a === hi && b === li);
      const [lo, hiIdx] = ia < ib ? [ia, ib] : [ib, ia];
      for (let i = lo; i <= hiIdx; i++) this.picked.add(flat[i].join(':'));
    } else if (this.picked.has(key)) this.picked.delete(key);
    else this.picked.add(key);
    this.anchor = [hi, li];
    const top = this.scroll.scrollTop;
    this.render();
    this.scroll.scrollTop = top;
    this.el.focus({ preventScroll: true });
  }

  clearSelection() { this.picked.clear(); this.anchor = null; if (this.diff) this.render(); }

  selection() {
    if (!this.diff || !this.picked.size) return null;
    return { hunks: [], lines: [...this.picked].map((k) => k.split(':').map(Number)) };
  }

  updateBar() {
    const n = this.picked.size;
    const acts = n ? this.selectionActions(this.diff) : [];
    this.selBar.hidden = !n || !acts.length;
    if (this.selBar.hidden) return;
    fill(this.selBar, h('span', {}, `${n} line${n === 1 ? '' : 's'} selected`),
      ...acts.map((a) => h('button', {
        class: `btn ${a.primary ? 'primary' : a.danger ? 'danger' : ''}`,
        onclick: () => this.onAction(a.id, this.selection()),
      }, a.label)),
      h('button', { class: 'mini', title: 'Clear selection (Esc)', onclick: () => this.clearSelection() }, '✕'));
  }
}
