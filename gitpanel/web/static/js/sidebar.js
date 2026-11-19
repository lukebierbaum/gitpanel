// Branch sidebar: local / remote / tags. Click filters the log (ctrl/⌘-click marks several),
// double-click checks out, right-click shows branch actions.
import { call } from './rpc.js';
import { h, clear, icon, store } from './dom.js';
import { toast } from './overlays.js';

export class Sidebar {
  constructor(app) {
    this.app = app;
    this.el = document.getElementById('branch-list');
    this.filter = document.getElementById('branch-filter');
    this.data = { local: [], remote: [], tags: [], current: null };
    this.items = []; // [{name, filter}] in display order (null name = "All branches")
    this.cursor = 0;
    this.marked = new Set(); // filters (short names) applied to the log
    this.collapsed = new Set(store('side.collapsed') || ['tags']);
    this.el.classList.add('focus-region');
    this.el.tabIndex = 0;
    this.el.addEventListener('focus', () => { this.app.setFocusRegion('branches'); this.app.contextChanged(); });
    this.el.addEventListener('keydown', (e) => this.key(e));
    this.filter.addEventListener('input', () => this.render());
    this.filter.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { this.filter.value = ''; this.render(); this.el.focus(); e.stopPropagation(); }
      if (e.key === 'ArrowDown' || e.key === 'Enter') { e.preventDefault(); this.el.focus(); }
    });
  }

  async reload() {
    try {
      this.data = await call('branches.list');
    } catch (e) {
      toast(e.message, 'error');
      return;
    }
    this.render();
  }

  render() {
    const q = this.filter.value.trim().toLowerCase();
    const match = (b) => !q || b.short.toLowerCase().includes(q);
    clear(this.el);
    this.items = [];
    const add = (el, item) => { el.dataset.idx = this.items.length; this.items.push(item); this.el.append(el); };

    add(this.item({ label: 'All branches', ico: 'all', cls: 'side-all', selected: !this.marked.size }), { name: null, filter: null });

    const section = (id, title, list, build) => {
      const shown = list.filter(match);
      if (!shown.length) return;
      const collapsed = this.collapsed.has(id) && !q;
      this.el.append(h('div', {
        class: `side-head ${collapsed ? 'collapsed' : ''}`,
        onclick: () => { this.collapsed.has(id) ? this.collapsed.delete(id) : this.collapsed.add(id); store('side.collapsed', [...this.collapsed]); this.render(); },
      }, h('span', { class: 'chev' }, '▾'), title, h('span', { class: 'n' }, shown.length)));
      if (!collapsed) for (const b of shown) build(b);
    };

    const local = [...this.data.local].sort((a, b) => (b.current - a.current) || a.short.localeCompare(b.short));
    section('local', 'Local', local, (b) => add(this.item({
      label: b.short, ico: 'branch', current: b.current, ref: b,
      chips: [b.ahead ? h('span', { class: 'chip-up' }, `↑${b.ahead}`) : null, b.behind ? h('span', { class: 'chip-down' }, `↓${b.behind}`) : null,
        b.gone ? h('span', { class: 'chip-gone', title: 'upstream is gone' }, '✕') : null],
      title: [b.short, b.upstream ? `→ ${b.upstream}` : 'no upstream', b.subject].join('\n'),
    }), { name: b.name, filter: b.short }));

    const byRemote = new Map();
    for (const b of this.data.remote) {
      const r = b.short.split('/')[0];
      if (!byRemote.has(r)) byRemote.set(r, []);
      byRemote.get(r).push(b);
    }
    for (const [remote, list] of byRemote) {
      section(`remote:${remote}`, `Remote · ${remote}`, list, (b) => add(this.item({
        label: b.short.slice(remote.length + 1), ico: 'remote', ref: b, title: `${b.short}\n${b.subject}`,
      }), { name: b.name, filter: b.short }));
    }
    section('tags', 'Tags', [...this.data.tags].reverse(), (t) => add(this.item({
      label: t.short, ico: 'tag', ref: t, title: t.short,
    }), { name: t.name, filter: t.short }));
    this.cursor = Math.min(this.cursor, this.items.length - 1);
    this.markCursor(false);
  }

  item({ label, ico, cls = '', current = false, chips = [], title, ref, selected }) {
    const filter = ref ? ref.short : null;
    const isSel = selected ?? (filter && this.marked.has(filter));
    const el = h('div', {
      class: `side-item ${cls} ${current ? 'current' : ''} ${isSel ? 'filtered selected' : ''}`, title,
      onclick: (e) => this.click(Number(el.dataset.idx), e),
      ondblclick: () => { if (ref && !ref.name.startsWith('refs/tags/')) this.app.run('branch.checkout', this.ctxFor(ref.name)); },
      oncontextmenu: (e) => {
        e.preventDefault();
        if (!ref) return;
        this.cursor = Number(el.dataset.idx);
        this.markCursor(false);
        this.el.focus({ preventScroll: true });
        this.app.menuAt(e.clientX, e.clientY, this.ctxFor(ref.name));
      },
    }, icon(ico), h('span', { class: 'label' }, label), chips.some(Boolean) ? h('span', { class: 'chips' }, ...chips) : null);
    return el;
  }

  click(idx, e) {
    this.cursor = idx;
    const it = this.items[idx];
    if (!it.filter) this.marked.clear();
    else if (e.ctrlKey || e.metaKey) this.marked.has(it.filter) ? this.marked.delete(it.filter) : this.marked.add(it.filter);
    else this.marked = new Set([it.filter]);
    this.apply();
    this.el.focus({ preventScroll: true });
  }

  apply() {
    this.render();
    this.app.showView('log');
    this.app.log.setBranchFilter([...this.marked]);
    this.app.contextChanged();
  }

  selectAll() { this.marked.clear(); this.cursor = 0; this.apply(); }

  markCursor(scroll) {
    this.el.querySelectorAll('.side-item').forEach((el) => el.classList.toggle('cursor', Number(el.dataset.idx) === this.cursor));
    if (scroll) this.el.querySelector('.side-item.cursor')?.scrollIntoView({ block: 'nearest' });
  }

  key(e) {
    const d = { ArrowDown: 1, j: 1, ArrowUp: -1, k: -1 }[e.key];
    if (d && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      e.stopPropagation();
      this.cursor = Math.max(0, Math.min(this.items.length - 1, this.cursor + d));
      this.markCursor(true);
      this.app.contextChanged();
      return;
    }
    if (e.key === ' ' && !e.ctrlKey) {
      // like the TUI: space marks the branch for the log filter
      e.preventDefault();
      e.stopPropagation();
      this.click(this.cursor, { ctrlKey: true });
    }
  }

  ctxFor(name) { return { screen: 'log', focus: 'branch', refs: [name], commits: [] }; }

  ctx() {
    const it = this.items[this.cursor];
    if (!it || !it.name) return { screen: 'log' };
    return this.ctxFor(it.name);
  }
}
