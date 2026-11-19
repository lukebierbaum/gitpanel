// Changes view (the TUI's Commit tool window): file groups, hunk/line staging, commit box.
import { call } from './rpc.js';
import { h, clear, splitter, store } from './dom.js';
import { DiffView } from './diff.js';
import { fileRow } from './log.js';
import { toast } from './overlays.js';

const GROUPS = [
  ['conflict', 'Merge Conflicts'],
  ['staged', 'Staged'],
  ['unstaged', 'Changes'],
  ['untracked', 'Unversioned Files'],
];

export class ChangesView {
  constructor(app, root) {
    this.app = app;
    this.root = root;
    this.groups = {};
    this.flat = []; // [{group, path, ...}]
    this.current = null; // key "group\0path"
    this.focus = 'files';
    this.stale = true;
    this.build();
  }

  build() {
    this.filesEl = h('div', { class: 'changes-files focus-region', tabindex: '0',
      onfocus: () => this.setFocus('files'), onkeydown: (e) => this.filesKey(e) });
    this.msg = h('textarea', { placeholder: 'Commit message', spellcheck: 'true', onfocus: () => this.setFocus('message') });
    this.amend = h('input', { type: 'checkbox', onchange: () => this.amendChanged() });
    this.signoff = h('input', { type: 'checkbox' });
    this.commitBtn = h('button', { class: 'btn go', onclick: () => this.app.run('commit.commit', this.ctx('global')) }, 'Commit', h('kbd', {}, '⌃K'));
    this.pushBtn = h('button', { class: 'btn', onclick: () => this.app.run('commit.commit_and_push', this.ctx('global')) }, 'Commit and Push…');
    const box = h('div', { class: 'commit-box' }, this.msg,
      h('div', { class: 'commit-opts' }, h('label', {}, this.amend, 'Amend'), h('label', {}, this.signoff, 'Sign-off')),
      h('div', { class: 'commit-btns' }, this.commitBtn, this.pushBtn));
    this.left = h('div', { class: 'changes-left' },
      h('div', { class: 'pane-title' }, 'Local changes', h('span', { class: 'grow' }),
        h('button', { class: 'mini', title: 'Refresh (ctrl+R)', onclick: () => this.app.run('app.refresh', this.ctx('global')) }, '↻')),
      this.filesEl, box);
    this.diff = new DiffView({
      onFocus: () => this.setFocus('diff'),
      hunkActions: () => this.patchActions(false),
      selectionActions: () => this.patchActions(true),
      onAction: (id, sel) => this.app.run(id, this.patchCtx(sel)),
    });
    this.diff.el.addEventListener('contextmenu', (e) => { e.preventDefault(); this.setFocus('diff'); this.app.menuAt(e.clientX, e.clientY, this.ctx()); });
    const split = h('div', { class: 'splitter-x' });
    this.grid = h('div', { class: 'changes' }, this.left, split, this.diff.el);
    this.root.append(this.grid);
    const w = store('changes.w');
    if (w) this.grid.style.setProperty('--changes-w', `${w}px`);
    splitter(split, {
      axis: 'x', get: () => this.left.getBoundingClientRect().width, min: 280,
      max: () => this.grid.getBoundingClientRect().width - 300,
      set: (v) => { this.grid.style.setProperty('--changes-w', `${v}px`); store('changes.w', v); },
    });
  }

  patchActions(lines) {
    const g = this.cur()?.group;
    const what = lines ? 'lines' : 'hunk';
    if (g === 'staged') return [{ id: 'commit.unstage', label: `Unstage ${what}`, primary: true }];
    if (g === 'unstaged' || g === 'untracked') {
      return [
        { id: 'commit.stage', label: `Stage ${what}`, primary: true },
        { id: 'commit.rollback', label: 'Rollback', danger: true, title: `Discard these changes (undoable)` },
      ];
    }
    return [];
  }

  // -- data -----------------------------------------------------------------------------
  async reload() {
    this.stale = false;
    let groups;
    try {
      groups = await call('changes.status');
    } catch (e) {
      toast(e.message, 'error');
      return;
    }
    const prevIdx = this.flat.findIndex((f) => this.key(f) === this.current);
    this.groups = groups;
    this.flat = GROUPS.flatMap(([g]) => groups[g] || []);
    if (!this.flat.some((f) => this.key(f) === this.current)) {
      // the file moved groups (e.g. staged): follow it, else keep the position
      const path = this.current?.split('\0')[1];
      const same = this.flat.find((f) => f.path === path);
      const next = same || this.flat[Math.min(Math.max(prevIdx, 0), this.flat.length - 1)];
      this.current = next ? this.key(next) : null;
    }
    this.render();
    this.showDiff(true);
    this.app.setChangesCount(this.flat.length);
  }

  key(f) { return `${f.group}\0${f.path}`; }
  cur() { return this.flat.find((f) => this.key(f) === this.current) || null; }

  render() {
    clear(this.filesEl);
    if (!this.flat.length) {
      this.filesEl.append(h('div', { class: 'empty' }, h('div', {}, h('div', { class: 'big' }, 'Working tree clean'), 'Nothing to commit.')));
      return;
    }
    for (const [g, title] of GROUPS) {
      const files = this.groups[g] || [];
      if (!files.length) continue;
      const bulk = [];
      if (g === 'staged') bulk.push(h('button', { class: 'mini', onclick: () => this.app.run('commit.unstage_all', this.ctx('global')) }, 'Unstage all'));
      if (g === 'unstaged' || g === 'untracked') bulk.push(h('button', { class: 'mini', onclick: () => this.app.run('commit.stage_all', this.ctx('global')) }, 'Stage all'));
      this.filesEl.append(h('div', { class: `grp-head ${g}` }, title, h('span', { class: 'n' }, files.length), h('span', { class: 'grow' }), ...bulk));
      for (const f of files) {
        const fileCtx = () => this.ctx('file', f);
        const acts = [];
        if (g === 'staged') acts.push(h('button', { class: 'mini', title: 'Unstage', onclick: (e) => { e.stopPropagation(); this.app.run('commit.unstage', fileCtx()); } }, '−'));
        else acts.push(h('button', { class: 'mini', title: 'Stage', onclick: (e) => { e.stopPropagation(); this.app.run('commit.stage', fileCtx()); } }, '+'));
        if (g !== 'conflict') acts.push(h('button', { class: 'mini danger', title: 'Rollback', onclick: (e) => { e.stopPropagation(); this.app.run('commit.rollback', fileCtx()); } }, '↺'));
        this.filesEl.append(fileRow(f, {
          selected: this.key(f) === this.current,
          actions: acts,
          onclick: () => this.pick(f),
          oncontextmenu: (e) => { e.preventDefault(); this.pick(f); this.app.menuAt(e.clientX, e.clientY, this.ctx()); },
        }));
      }
    }
  }

  pick(f) {
    this.current = this.key(f);
    this.render();
    this.filesEl.focus({ preventScroll: true });
    this.setFocus('files');
    this.showDiff(false);
  }

  filesKey(e) {
    const d = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key];
    if (!d || !this.flat.length || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    e.stopPropagation();
    const i = this.flat.findIndex((f) => this.key(f) === this.current);
    const next = this.flat[Math.max(0, Math.min(this.flat.length - 1, i + d))];
    this.current = this.key(next);
    this.render();
    this.filesEl.querySelector('.frow.selected')?.scrollIntoView({ block: 'nearest' });
    this.showDiff(false);
    this.app.contextChanged();
  }

  async showDiff(keep) {
    const f = this.cur();
    if (!f) { this.diff.empty(this.flat.length ? 'Select a file' : 'No local changes'); return; }
    const source = { kind: f.group };
    if (!keep) this.diff.loading();
    const want = this.current;
    const diff = await call('diff.file', { source, path: f.path }).catch((e) => { toast(e.message, 'error'); return null; });
    if (this.current !== want) return;
    if (diff) this.diff.set(diff, source, { keepSelection: keep });
    else this.diff.empty(f.group === 'conflict' ? 'Conflicted file — resolve it in the TUI for now' : 'No diff');
  }

  async amendChanged() {
    if (this.amend.checked && !this.msg.value.trim()) {
      this.msg.value = await call('changes.last_message').catch(() => '');
    }
    this.commitBtn.firstChild.textContent = this.amend.checked ? 'Amend Commit' : 'Commit';
  }

  // -- context ---------------------------------------------------------------------------
  setFocus(region) {
    this.focus = region;
    this.app.setFocusRegion(region);
    this.app.contextChanged();
  }

  form() {
    return { message: this.msg.value, amend: this.amend.checked, signoff: this.signoff.checked, author: '' };
  }

  ctx(force, file) {
    const base = { screen: 'commit', form: this.form() };
    const f = file || this.cur();
    if (force === 'global') return base;
    if ((force === 'file' || this.focus === 'files') && f) return { ...base, focus: 'file', files: [{ group: f.group, path: f.path }] };
    if (this.focus === 'diff' && f) {
      const sel = this.diff.selection();
      if (sel) return this.patchCtx(sel);
      return { ...base, focus: 'file', files: [{ group: f.group, path: f.path }] };
    }
    return base;
  }

  patchCtx(sel) {
    const f = this.cur();
    return {
      screen: 'commit', form: this.form(), focus: 'hunk', files: [{ group: f.group, path: f.path }],
      patch: { source: this.diff.source, path: this.diff.diff.path, ...sel },
    };
  }

  handleKey(e) {
    if (e.key === 'Escape' && this.focus === 'diff' && this.diff.picked.size) { this.diff.clearSelection(); return true; }
    return false;
  }

  focusDefault() { this.filesEl.focus({ preventScroll: true }); }

  onPost(event) {
    if (event === 'commit.done') {
      this.msg.value = '';
      this.amend.checked = false;
      this.amendChanged();
    } else if (event === 'commit.toggle_amend') {
      this.amend.checked = !this.amend.checked;
      this.amendChanged();
    }
  }

  onChanged() { this.reload(); }
}
