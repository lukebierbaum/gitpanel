// Log view: filters, commit list with graph, details, changed files and diff.
import { call } from './rpc.js';
import { h, svg, clear, fill, relDate, fullDate, avatar, splitPath, debounce, splitter, store } from './dom.js';
import { drawGraph, graphWidth } from './graph.js';
import { DiffView } from './diff.js';
import { toast } from './overlays.js';

const ROW_H = 30;

export function badge(m) {
  if (!m.label) return null;
  return h('span', { class: `badge ${m.style || m.kind}`, title: m.label }, m.label);
}

export function fileRow(f, { selected, onclick, oncontextmenu, actions = [] }) {
  const [dir, name] = splitPath(f.path);
  return h('div', { class: `frow ${selected ? 'selected' : ''}`, onclick, oncontextmenu, title: f.path },
    h('span', { class: `st st-${f.status}` }, f.status),
    h('span', { class: 'fname' }, name),
    h('span', { class: 'fdir' }, f.old_path ? `← ${f.old_path}` : dir),
    actions.length ? h('span', { class: 'acts' }, ...actions) : null,
    f.additions != null && !f.binary
      ? h('span', { class: 'stat' }, h('span', { class: 'a' }, `+${f.additions}`), ' ', h('span', { class: 'd' }, `−${f.deletions}`))
      : null);
}

export class LogView {
  constructor(app, root) {
    this.app = app;
    this.root = root;
    this.rows = []; // row JSON from log.page
    this.bySha = new Map();
    this.rowEls = new Map();
    this.selected = new Set();
    this.cursor = -1;
    this.anchor = -1;
    this.hasMore = false;
    this.gen = 0;
    this.loadingMore = false;
    this.maxLane = 0;
    this.focus = 'commits';
    this.detailSha = null;
    this.files = [];
    this.fileIdx = -1;
    this.stale = true;
    this.build();
  }

  build() {
    const input = (id, ph) => h('input', { id, placeholder: ph, spellcheck: 'false', onkeydown: (e) => this.filterKey(e) });
    this.fText = input('f-text', 'Search messages (regex)  /');
    this.fUser = input('f-user', 'Author');
    this.fPath = input('f-path', 'Paths');
    this.fSince = input('f-since', 'Since (2.weeks)');
    this.fBranch = { value: '' };
    this.legend = h('div', { class: 'legend', hidden: true },
      h('span', {}, h('i', { style: { background: 'var(--amber)' } }), 'not pushed'),
      h('span', {}, h('i', { style: { border: '2px solid var(--purple)', width: '9px', height: '9px' } }), 'remote only'));
    const search = h('div', { class: 'f-text' },
      svg('svg', { viewBox: '0 0 16 16' }, svg('circle', { cx: 7, cy: 7, r: 4.5 }), svg('path', { d: 'm10.5 10.5 3 3' })),
      this.fText);

    this.branchPill = h('button', { class: 'mini', hidden: true, title: 'Clear branch filter', onclick: () => this.app.sidebar.selectAll() });
    this.list = h('div', { class: 'commit-list focus-region', tabindex: '0',
      onfocus: () => this.setFocus('commits'),
      onkeydown: (e) => this.listKey(e),
      onscroll: () => this.maybeMore() });
    this.head = h('div', { class: 'commit-head' }, h('span', {}), h('span', {}, 'Commit'), h('span', { class: 'h-author' }, 'Author'),
      h('span', {}, 'Date'), h('span', { class: 'h-sha' }, 'Hash'));
    this.top = h('div', { class: 'log-top' }, this.head, this.list);

    this.detailsBody = h('div', { class: 'details-body' });
    this.fileList = h('div', { class: 'file-list focus-region', tabindex: '0', onfocus: () => this.setFocus('files'), onkeydown: (e) => this.filesKey(e) });
    this.filesTitle = h('div', { class: 'pane-title' }, 'Changed files', h('span', { class: 'grow' }));
    this.details = h('div', { class: 'details' }, this.detailsBody, this.filesTitle, this.fileList);
    this.diff = new DiffView({
      onFocus: () => this.setFocus('diff'),
      selectionActions: () => [
        { id: 'log.apply_changes', label: 'Apply to working tree', primary: true },
        { id: 'patch.remove', label: 'Remove from commit', danger: true },
      ],
      onAction: (id, sel) => this.app.run(id, this.patchCtx(sel)),
    });
    this.diff.el.addEventListener('contextmenu', (e) => { e.preventDefault(); this.setFocus('diff'); this.app.menuAt(e.clientX, e.clientY, this.ctx()); });
    const splitX = h('div', { class: 'splitter-x' });
    this.bottom = h('div', { class: 'log-bottom' }, this.details, splitX, this.diff.el);
    const splitY = h('div', { class: 'splitter-y' });
    this.root.append(h('div', { class: 'filters' }, search, this.branchPill, this.fUser, this.fPath, this.fSince, this.legend), this.top, splitY, this.bottom);

    const topH = store('log.top');
    if (topH) { this.top.style.flex = `0 0 ${topH}px`; }
    splitter(splitY, {
      axis: 'y', get: () => this.top.getBoundingClientRect().height, min: 90,
      max: () => this.root.getBoundingClientRect().height - 160,
      set: (v) => { this.top.style.flex = `0 0 ${v}px`; store('log.top', v); },
    });
    const detW = store('log.details');
    if (detW) this.bottom.style.gridTemplateColumns = `${detW}px 5px 1fr`;
    splitter(splitX, {
      axis: 'x', get: () => this.details.getBoundingClientRect().width, min: 220,
      max: () => this.bottom.getBoundingClientRect().width - 240,
      set: (v) => { this.bottom.style.gridTemplateColumns = `${v}px 5px 1fr`; store('log.details', v); },
    });
    this.showDetailsSoon = debounce(() => this.showDetails(), 90);
  }

  // -- loading ---------------------------------------------------------------------
  filter() {
    return { text: this.fText.value, author: this.fUser.value, paths: this.fPath.value, since: this.fSince.value, branch: this.fBranch.value };
  }

  setBranchFilter(names) {
    this.fBranch.value = names.join(' ');
    this.branchPill.hidden = !names.length;
    fill(this.branchPill, `⎇ ${names.join(', ')}  ✕`);
    this.reload();
  }

  async reload() {
    this.stale = false;
    const gen = ++this.gen;
    const keep = [...this.selected];
    const cursorSha = this.rows[this.cursor]?.sha;
    let res;
    try {
      res = await call('log.page', { filter: this.filter(), offset: 0, gen });
    } catch (e) {
      toast(e.message, 'error');
      res = { rows: [], has_more: false };
    }
    if (gen !== this.gen) return;
    this.rows = res.rows;
    this.hasMore = res.has_more;
    this.render();
    // keep the selection when its commits are still there, else select the first row
    const still = keep.filter((s) => this.bySha.has(s));
    if (still.length) {
      this.selected = new Set(still);
      this.cursor = this.rows.findIndex((r) => r.sha === (this.bySha.has(cursorSha) ? cursorSha : still[0]));
      this.anchor = this.cursor;
      this.markSelection(false);
      this.detailSha = null;
      this.showDetails();
    } else if (this.rows.length) this.select(0, {});
    else this.showDetails();
    this.slowMarkers(res.rows);
  }

  async maybeMore() {
    if (!this.hasMore || this.loadingMore) return;
    const el = this.list;
    if (el.scrollTop + el.clientHeight < el.scrollHeight - ROW_H * 20) return;
    this.loadingMore = true;
    const gen = this.gen;
    try {
      const res = await call('log.page', { filter: this.filter(), offset: this.rows.length, gen });
      if (res.stale || gen !== this.gen) return;
      this.rows.push(...res.rows);
      this.hasMore = res.has_more;
      this.appendRows(res.rows);
      this.slowMarkers(res.rows);
    } finally {
      this.loadingMore = false;
    }
  }

  async slowMarkers(rows) {
    if (!rows.length) return;
    const extra = await call('log.slow_markers', { shas: rows.map((r) => r.sha) }).catch(() => ({}));
    for (const [sha, markers] of Object.entries(extra)) {
      const r = this.bySha.get(sha);
      if (!r) continue;
      r.markers = markers;
      const old = this.rowEls.get(sha);
      if (old) {
        const fresh = this.rowEl(r, this.rows.indexOf(r));
        old.replaceWith(fresh);
        this.rowEls.set(sha, fresh);
      }
    }
    this.legend.hidden = !this.rows.some((r) => r.markers.some((m) => m.node));
    if (this.detailSha && extra[this.detailSha]) { this.detailSha = null; this.showDetails(); }
  }

  // -- rendering -----------------------------------------------------------------------
  render() {
    this.bySha = new Map(this.rows.map((r) => [r.sha, r]));
    this.rowEls = new Map();
    this.maxLane = 0;
    clear(this.list);
    if (!this.rows.length) {
      this.list.append(h('div', { class: 'empty' }, h('div', {}, h('div', { class: 'big' }, 'No commits match'), 'Try clearing the filters.')));
      return;
    }
    this.appendRows(this.rows);
    this.list.scrollTop = 0;
  }

  appendRows(rows) {
    for (const r of rows) {
      this.bySha.set(r.sha, r);
      for (const [, lane] of r.graph.edges) this.maxLane = Math.max(this.maxLane, lane);
      this.maxLane = Math.max(this.maxLane, r.graph.col);
    }
    this.root.style.setProperty('--graph-w', `${graphWidth(this.maxLane)}px`);
    const frag = document.createDocumentFragment();
    const start = this.rows.length - rows.length;
    rows.forEach((r, i) => {
      const el = this.rowEl(r, start + i);
      this.rowEls.set(r.sha, el);
      frag.append(el);
    });
    this.list.append(frag);
  }

  rowEl(r, idx) {
    const merge = r.parents.length > 1;
    const dim = r.markers.some((m) => m.dims);
    const el = h('div', {
      class: `crow ${merge ? 'merge' : ''} ${dim ? 'dim' : ''} ${this.selected.has(r.sha) ? 'selected' : ''} ${idx === this.cursor ? 'cursor' : ''}`,
      dataset: { sha: r.sha },
      onmousedown: (e) => { if (e.button === 0) this.click(idx, e); },
      oncontextmenu: (e) => {
        e.preventDefault();
        if (!this.selected.has(r.sha)) this.select(idx, {});
        this.list.focus({ preventScroll: true });
        this.app.menuAt(e.clientX, e.clientY, this.ctx());
      },
    },
    drawGraph(r.graph, r.markers, ROW_H),
    h('div', { class: 'subj' }, ...r.markers.map(badge), h('span', { class: 'text', title: r.subject }, r.subject)),
    h('div', { class: 'author', title: r.email }, r.author),
    h('div', { class: 'date', title: fullDate(r.date) }, relDate(r.date)),
    h('div', { class: 'sha' }, r.short));
    return el;
  }

  // -- selection ------------------------------------------------------------------------
  click(idx, e) {
    if (e.shiftKey && this.anchor >= 0) this.select(idx, { range: true });
    else if (e.ctrlKey || e.metaKey) this.select(idx, { toggle: true });
    else this.select(idx, {});
    this.list.focus({ preventScroll: true });
  }

  select(idx, { range = false, toggle = false }) {
    if (idx < 0 || idx >= this.rows.length) return;
    const sha = this.rows[idx].sha;
    if (range) {
      const [a, b] = this.anchor < idx ? [this.anchor, idx] : [idx, this.anchor];
      this.selected = new Set(this.rows.slice(a, b + 1).map((r) => r.sha));
    } else if (toggle) {
      if (this.selected.has(sha) && this.selected.size > 1) this.selected.delete(sha);
      else this.selected.add(sha);
      this.anchor = idx;
    } else {
      this.selected = new Set([sha]);
      this.anchor = idx;
    }
    this.cursor = idx;
    this.markSelection(true);
    this.showDetailsSoon();
    this.app.contextChanged();
  }

  markSelection(scroll) {
    for (const [sha, el] of this.rowEls) el.classList.toggle('selected', this.selected.has(sha));
    this.list.querySelector('.crow.cursor')?.classList.remove('cursor');
    const cur = this.rows[this.cursor] && this.rowEls.get(this.rows[this.cursor].sha);
    if (cur) {
      cur.classList.add('cursor');
      if (scroll) cur.scrollIntoView({ block: 'nearest' });
    }
  }

  selectSha(sha) {
    const idx = this.rows.findIndex((r) => r.sha === sha || r.sha.startsWith(sha));
    if (idx >= 0) this.select(idx, {});
    return idx >= 0;
  }

  listKey(e) {
    const page = Math.max(1, Math.floor(this.list.clientHeight / ROW_H) - 1);
    const moves = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1, PageDown: page, PageUp: -page };
    if (e.key in moves && !e.ctrlKey && !e.altKey && !e.metaKey) {
      e.preventDefault();
      e.stopPropagation();
      const idx = Math.max(0, Math.min(this.rows.length - 1, this.cursor + moves[e.key]));
      this.select(idx, { range: e.shiftKey });
      if (idx > this.rows.length - 40) this.maybeMore();
      return;
    }
    if (e.key === 'Home' || e.key === 'End') {
      e.preventDefault();
      e.stopPropagation();
      this.select(e.key === 'Home' ? 0 : this.rows.length - 1, { range: e.shiftKey });
    }
  }

  filterKey(e) {
    if (e.key === 'Enter') { e.preventDefault(); this.reload(); this.list.focus(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); e.target.value = ''; this.reload(); this.list.focus(); }
  }

  // -- details ----------------------------------------------------------------------------
  async showDetails() {
    const shas = [...this.selected];
    if (shas.length !== 1) {
      this.detailSha = null;
      fill(this.detailsBody, h('div', { class: 'empty', style: { height: 'auto' } },
        shas.length ? `${shas.length} commits selected` : (this.rows.length ? '' : 'No commits')));
      clear(this.fileList);
      this.diff.empty(shas.length > 1 ? 'Select a single commit to see its changes' : 'Nothing selected');
      return;
    }
    const sha = shas[0];
    if (sha === this.detailSha) return;
    this.detailSha = sha;
    const row = this.bySha.get(sha);
    let d;
    try {
      d = await call('log.details', { sha });
    } catch (e) {
      toast(e.message, 'error');
      return;
    }
    if (this.detailSha !== sha) return;
    const reach = h('div', { class: 'reach' });
    if (d.has_remotes) {
      reach.append(d.pushed_to.length
        ? h('div', { class: 'row' }, h('span', { class: 'ok' }, '✓ Pushed'), d.pushed_to.slice(0, 6).join(', ') + (d.pushed_to.length > 6 ? ` +${d.pushed_to.length - 6}` : ''))
        : h('div', { class: 'row' }, h('span', { class: 'warn' }, '↑ Not pushed to any remote')));
    }
    if (d.branches.length) reach.append(h('div', { class: 'row' }, h('span', {}, 'In'), d.branches.slice(0, 12).join(', ') + (d.branches.length > 12 ? ` +${d.branches.length - 12}` : '')));
    else if (!d.pushed_to.length) reach.append(h('div', { class: 'row' }, h('span', { class: 'inc' }, '↓ Not on any local branch (remote only)')));
    const committer = d.committer && d.committer !== d.author ? h('span', {}, `committed by ${d.committer} · ${relDate(d.commit_date)}`) : null;
    fill(this.detailsBody, 
      h('h2', {}, d.subject),
      d.body ? h('pre', {}, d.body) : null,
      h('div', { class: 'meta' }, avatar(d.author), h('span', { class: 'who', title: d.email }, d.author),
        h('span', { title: fullDate(d.date) }, relDate(d.date)), committer,
        h('span', { class: 'sha-copy', title: 'Copy full hash', onclick: () => this.copy(d.sha) }, d.short)),
      row && row.markers.some((m) => m.label) ? h('div', { class: 'badges' }, ...row.markers.map(badge)) : null,
      reach);
    this.files = d.files;
    this.fileIdx = this.files.length ? 0 : -1;
    fill(this.filesTitle, `Changed files`, h('span', { class: 'grow' }),
      h('span', { class: 'mono', style: { textTransform: 'none', letterSpacing: 0 } }, `${d.files.length}`));
    this.renderFiles();
    if (this.fileIdx >= 0) this.showFile();
    else this.diff.empty(d.parents.length > 1 ? 'Merge commit — no changes against the first parent' : 'No file changes');
  }

  copy(text) {
    navigator.clipboard?.writeText(text).then(() => toast(`Copied ${text.slice(0, 12)}`), () => toast('Clipboard not available', 'warning'));
  }

  renderFiles() {
    fill(this.fileList, ...this.files.map((f, i) => fileRow(f, {
      selected: i === this.fileIdx,
      onclick: () => { this.fileIdx = i; this.renderFiles(); this.showFile(); this.fileList.focus({ preventScroll: true }); },
      oncontextmenu: (e) => {
        e.preventDefault();
        this.fileIdx = i; this.renderFiles(); this.showFile();
        this.fileList.focus({ preventScroll: true });
        this.app.menuAt(e.clientX, e.clientY, this.ctx());
      },
    })));
  }

  filesKey(e) {
    const d = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key];
    if (!d || !this.files.length || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    e.stopPropagation();
    this.fileIdx = Math.max(0, Math.min(this.files.length - 1, this.fileIdx + d));
    this.renderFiles();
    this.fileList.children[this.fileIdx]?.scrollIntoView({ block: 'nearest' });
    this.showFile();
  }

  async showFile() {
    const f = this.files[this.fileIdx];
    const sha = this.detailSha;
    if (!f || !sha) return;
    this.diff.loading();
    const source = { kind: 'commit', sha };
    const diff = await call('diff.file', { source, path: f.path }).catch((e) => { toast(e.message, 'error'); return null; });
    if (this.detailSha !== sha || this.files[this.fileIdx] !== f) return;
    if (diff) this.diff.set(diff, source);
    else this.diff.empty('No diff');
    this.app.contextChanged();
  }

  // -- context ----------------------------------------------------------------------------------
  setFocus(region) {
    this.focus = region;
    this.app.setFocusRegion(region);
    this.app.contextChanged();
  }

  ctx() {
    const commits = this.rows.filter((r) => this.selected.has(r.sha)).map((r) => r.sha);
    const base = { screen: 'log', commits };
    if (this.focus === 'files' && this.files[this.fileIdx] && this.detailSha) {
      return { ...base, focus: 'file', files: [{ group: 'commit', path: this.files[this.fileIdx].path, sha: this.detailSha }] };
    }
    if (this.focus === 'diff') {
      const sel = this.diff.selection();
      if (sel && this.diff.diff) return this.patchCtx(sel);
    }
    if (this.focus === 'commits' && commits.length) return { ...base, focus: 'commit' };
    return base;
  }

  patchCtx(sel) {
    const commits = [...this.selected];
    return {
      screen: 'log', commits, focus: 'hunk',
      patch: { source: this.diff.source, path: this.diff.diff.path, ...sel },
    };
  }

  handleKey(e) {
    if (e.key === '/' && !e.ctrlKey && !e.altKey) { this.fText.focus(); this.fText.select(); return true; }
    if (e.key === 'Escape' && this.focus === 'diff' && this.diff.picked.size) { this.diff.clearSelection(); return true; }
    return false;
  }

  focusDefault() { this.list.focus({ preventScroll: true }); }

  onChanged() {
    this.detailSha = null;
    this.reload();
  }
}
