# gitpanel roadmap — PyCharm-style Git TUI (Python + Textual)

> **This is the original build plan, kept for history.** Everything in it has shipped (milestones M1–M10); where the build differs from the plan, each [milestone spec](milestones/) has an "As built" section. For what gitpanel does today see the [README](../README.md); for what's left compared with PyCharm see [PARITY.md](PARITY.md).
>
> Companion docs: [architecture.md](architecture.md) (foundation contracts) · [milestones/](milestones/) (per-milestone specs, written at each milestone's kickoff) · [../CLAUDE.md](../CLAUDE.md) (working rules).

## Context
A terminal app that reproduces PyCharm's Git tooling (Commit tool window, Log with branch graph, Branches popup, remotes, merge-conflict resolver, history/annotate, shelve/stash, interactive rebase) — with **cherry-picking between branches** and **worktrees** as first-class workflows. Python + Textual TUI, all feature areas in scope. Repo: [`lukebierbaum/gitpanel`](https://github.com/lukebierbaum/gitpanel), MIT licensed.

### Compared with existing TUIs
| | lazygit | gitui | gitu | tig | gitpanel |
|---|---|---|---|---|---|
| Already-picked commit detection | ✗ | ✗ | ✗ | ✗ | ✅ |
| Cherry-pick to non-checked-out branch | ✗ | ✗ | ✗ | ✗ | ✅ |
| 3-pane conflict resolver | ◐ | ✗ | ✗ | ◐ | ✅ |
| Shelves / changelists | ✗ | ✗ | ✗ | ✗ | ✅ |
| Undo incl. discarded changes | ◐ | ✗ | ✗ | ✗ | ✅ |
| Worktrees | ✅ | ✗ | ✗ | ✗ | ✅ (full panel) |
| Bisect / patch builder / custom commands | ✅ | ✗ | ✗ | ◐ | ✅ (M10) |

## Status
All ten milestones are implemented — see each `docs/milestones/M*.md` "As built" section for deviations from this plan.

### Added after M10
- **Log:** branches panel beside the log (folders by `/`, multi-select, toggleable), graph dots for unpushed (amber) and remote-only (purple) commits, fast branch switching (picked detection via `--cherry-mark` over diverged commits, slow markers filled in after rows render, caches).
- **Checkout:** Smart Checkout (stash → switch → unstash) and Force Checkout (undoable) when local changes are in the way.
- **Console** tool window (`6`): every git command gitpanel runs, with stdin, output, exit code and timing.
- **Keys:** number keys `1`–`6` for tool windows (Alt+digit is taken by many terminals), several keys per action, Mac-style Option-layout characters mapped back to Alt, `?` key map.
- **Layout:** draggable, remembered pane dividers; side-by-side diff default from config.
- **Custom commands** accept arbitrary shell (only known `{placeholders}` are substituted).

### Next
- **M12 — Stacked pull requests:** GitHub stacks (`gh stack`) and not-yet-linked chains in the PR window, link / publish them as GitHub stacks, restack in one undo step, atomic stack merges ([plan](milestones/M12.md)).

The backlog is the ✗ / ◐ rows in [PARITY.md](PARITY.md) — top of the list: word-level diff highlighting and syntax colours, "diff with working tree", log navigation (go to hash, first-parent / hide merges), remembering the Update Project mode, managing remotes.

## Step 0 — Repo setup (done)
- ✅ `lukebierbaum/gitpanel` created; branch `main`.
- **Deps managed with pixi**, manifest in `pyproject.toml` under `[tool.pixi.*]` so the package stays pip/pipx-installable:
  - `[project]` (hatchling build): name `gitpanel`, `requires-python >=3.11`, deps `textual`, `rich`; script `gitpanel = gitpanel.__main__:main`.
  - `[tool.pixi.workspace]` channels `conda-forge`, platforms `linux-64`, `osx-arm64`, `osx-64`.
  - `[tool.pixi.dependencies]` `python = "3.12.*"`, `git` (pins a known git in the env for tests); `[tool.pixi.pypi-dependencies]` `gitpanel = { path = ".", editable = true }`.
  - `[tool.pixi.feature.dev.dependencies]` `pytest`, `pytest-asyncio`, `ruff`; `[tool.pixi.feature.dev.pypi-dependencies]` `textual-dev`; environments `default = ["dev"]`.
  - `[tool.pixi.tasks]`: `start = "gitpanel"`, `test = "pytest"`, `lint = "ruff check ."`, `fmt = "ruff format ."`, `dev = "textual run --dev gitpanel.ui.app:GitPanelApp"`, `demo = "bash scripts/make_demo_repo.sh"`.
  - Commit `pixi.lock` (generated in M1 once the package skeleton exists); `.gitignore` has `.pixi/`.

## Architecture
Shell out to the `git` CLI (machine-readable formats: `status --porcelain=v2 -z`, `log -z --format=…`, `for-each-ref --format`), never libgit2 — matches real git behavior incl. hooks/config. The tree below is the overview; [architecture.md](architecture.md) is authoritative for module boundaries and interfaces.

```
gitpanel/
  __main__.py            # argparse: `gitpanel [path]`, plus `gitpanel log|branches|commit` deep-links
  git/                   # pure backend, no UI imports, fully unit-tested
    runner.py            # async subprocess wrapper, GitError, env (GIT_EDITOR, LC_ALL=C), cancellation
    models.py            # dataclasses: FileChange, Commit, Ref, Hunk, Conflict, StashEntry, Shelf…
    status.py            # porcelain v2 parse → staged/unstaged/untracked/conflicted/ignored
    diff.py              # unified-diff parser → hunks/lines; build partial patch → `git apply --cached [-R]`
    changelists.py       # PyCharm changelists, persisted in .git/gitpanel/changelists.json
    commit.py            # commit, amend, sign-off, author override, rollback (restore), message history
    log.py               # paged log with filters (branch, author, date, path, text, hash)
    graph.py             # lane assignment for the commit graph → per-row glyphs + colors
    refs.py              # local/remote branches, tags, ahead/behind, favorites, recent branches
    remote.py            # fetch/pull(merge|rebase)/push(+force-with-lease, set-upstream), remotes CRUD
    cherry_pick.py       # see dedicated section
    merge_rebase.py      # merge, rebase onto, interactive rebase (GIT_SEQUENCE_EDITOR script), continue/abort/skip
    conflicts.py         # list unmerged, read :1/:2/:3 stages, write resolution, mark resolved
    history.py           # file history (`log --follow`), blame/annotate (`blame --porcelain`)
    stash.py / shelf.py  # git stash; PyCharm-style shelf = named patches in .git/gitpanel/shelf/
    reset_revert.py      # reset soft/mixed/hard/keep, revert, undo last commit, checkout revision
    tags.py
    worktrees.py         # `worktree list --porcelain -z`, add/remove/move/lock/unlock/prune/repair, per-worktree status
    journal.py           # undo/redo operation journal (.git/gitpanel/journal.jsonl), `mutation()` context manager
    operations.py        # OperationState: detect in-progress rebase/merge/cherry-pick/revert/bisect (+ progress n/m)
    patch.py             # source-agnostic hunk selection → Patch (from worktree, index, or any commit)
    scripted_rebase.py   # primitive: stop at commit(s), run a callback (amend/split/drop hunks), continue
    bisect.py            # start/good/bad/skip/reset/log/run
  actions/
    registry.py          # Action(id, label, default_key, when=context predicate, run=callable); single registry
    context.py           # SelectionContext: repo, worktree, commits[], branch/ref, files[], hunks[] — screens publish into it
    builtin.py           # all built-in actions registered here (screens don't hard-code bindings)
    custom.py            # user commands from config.toml → Actions with {commit} {branch} {file} {worktree} placeholders
  ui/
    app.py               # GitPanelApp: header, tool-window tabs, command palette + keymap fed by the action registry
    widgets/             # DiffView (unified + side-by-side, syntax highlight), GraphRow, FileTree, RefTree
    screens/
      commit.py          # Commit tool window
      log.py             # Log tab (graph + details + changed files + diff)
      branches.py        # Branches popup (modal)
      conflicts.py       # Merge conflicts list + 3-pane resolver
      worktrees.py       # Worktrees tool window
      history.py, annotate.py, shelf.py, rebase_editor.py, push.py, compare.py
    dialogs.py           # confirm, input, choice, progress/output log
tests/
  conftest.py            # tmp-repo factory fixture (commits, branches, conflicts, remotes via bare repo)
  git/test_*.py          # backend tests
  ui/test_*.py           # Textual `run_test()` pilot tests
```

## Extensibility foundations (built in milestones 1–2)
These cost little up front and keep later features (custom commands, patch builder, bisect) from forcing rewrites.
- **Action registry + selection context.** Every operation — built-in or user-defined — is an `Action` with an id, label, default key, a `when` predicate over the current `SelectionContext`, and a `run`. Key bindings, the command palette (Textual `CommandProvider`), context menus and custom commands all dispatch through it. Screens only publish their selection into the context; they never bind git operations directly. Gives key remapping (`[keys]` in config) for free.
- **Source-agnostic patches.** `patch.py` models a selection of files/hunks/lines over any diff source (`Worktree`, `Index`, `Commit(sha)`, `Range(a,b)`), producing a `Patch` that can be applied to index, worktree, or reverse-applied. Staging, rollback, shelving, "apply selected changes from revision" and the patch builder all use it.
- **Scripted rebase.** `scripted_rebase.run(base, edits={sha: callback})` drives `git rebase -i` via a `GIT_SEQUENCE_EDITOR` helper, stops at each target, runs the callback, continues; surfaces conflicts through the normal resolver. Used by the interactive-rebase editor, reword/fixup/drop from Log, and the patch builder.
- **Generic operation state.** `OperationState` (kind ∈ rebase/merge/cherry-pick/revert/bisect, progress, continue/skip/abort actions) drives one banner widget and conditions actions via `when`.
- **Row markers in Log.** Log rows carry a list of `Marker`s (ref labels, worktree HEADs, already-picked, bisect good/bad/skip, search hit) supplied by pluggable providers, so new decorations never touch the graph renderer.

## Feature map (PyCharm → gitpanel)

**Commit tool window** — tree of changes grouped by changelist / directory; checkboxes include files *and individual hunks/lines* (implemented as partial `git apply --cached`); unversioned files group; diff preview pane; commit message box with history; Amend toggle, Sign-off, Author; Commit / Commit and Push; Rollback (restore) file/hunk; add to .gitignore; move between changelists.

**Log** — graph column with colored lanes; columns: subject + ref labels, author, date; filters bar (branch, user, date, path, text/regex, hash); details pane (full message, refs, "contained in branches"); changed-files tree + diff for selected commit; multi-select. Context actions: cherry-pick, revert, reset current branch to here, checkout revision, new branch/tag here, interactively rebase from here, compare with local, copy hash, edit message (reword), fixup/squash into, drop.

**Branches popup** (`ctrl+shift+`` equivalent: `b`) — search, Recent / Local / Remote / Tags sections, favorites (★), ahead/behind indicators. Per-branch: checkout, new branch from, checkout & rebase onto current, compare with current, show diff with working tree, rebase current onto, merge into current, pull into current using rebase/merge, push, rename, delete (with restore offer), track/untrack upstream.

**Remotes** — fetch (all/one), update project (pull with merge or rebase, auto-stash), push dialog listing outgoing commits per branch with force-with-lease / tags / set-upstream; manage remotes (add/rename/remove/URL).

**Merge conflicts** — conflicts list (accept yours / accept theirs / merge…); 3-pane resolver: left=ours, center=result (editable), right=theirs, base available; per-chunk ←/→ apply, "apply all non-conflicting", conflict navigation, mark resolved → `git add`. Continue/abort for merge, rebase, cherry-pick, revert operations surfaced in a status banner.

**History & annotate** — file/folder history (with renames), selection history for line ranges (`log -L`), annotate gutter (author/date/hash, colored by age) with "show commit" and "annotate previous revision".

**Shelve / stash** — shelf: shelve selected changes/hunks with name, unshelve (whole or partial), delete; stash: push (incl. untracked/keep-index), apply/pop/drop/branch-from, view diff.

**Interactive rebase editor** — table of commits with pick/reword/edit/squash/fixup/drop, reorder with keys, reword inline; executed via a `GIT_SEQUENCE_EDITOR` helper that writes our todo; `GIT_EDITOR` helper for reword messages.

## Worktrees (tier 1)
Git is shared by all worktrees of a repo, so gitpanel treats the **active worktree** as app-level state: every screen operates on it, and switching re-roots the whole app in place (no restart).
- **Worktrees tool window** (`w`): table of all worktrees — path, branch / detached HEAD, dirty count (staged/unstaged/untracked), ahead/behind upstream, locked/prunable flags, operation in progress (rebase/merge/cherry-pick). Main worktree pinned first; active one marked.
- **Actions**: switch to (re-root app); new worktree → from existing branch, new branch (from selected ref), or detached at commit; default path template configurable (`../{repo}.worktrees/{branch}`); remove (refuse if dirty unless forced, offer stash/shelve first), move, lock/unlock with reason, prune stale, repair; open shell there (`$SHELL` suspended via `app.suspend()`); copy path.
- **Integration elsewhere**:
  - Branches popup shows `⎇ wt-name` on branches checked out in another worktree; "checkout" on such a branch offers *Switch to that worktree* instead of failing; "New worktree from branch" action.
  - Log: worktree HEADs drawn as ref labels; "New worktree at this commit"; commit tool window header shows active worktree.
  - Cherry-pick-to-other-branch (item 4 below) reuses an existing worktree for the target branch if there is one, else a temp worktree.
  - Compare worktrees: diff working tree of active vs another worktree (`git diff` across worktree paths for tracked files).
- **Shell `cd` on exit**: `gitpanel --cd-file FILE` writes the active worktree path on quit; README ships a `gp()` bash/zsh/fish wrapper that `cd`s there (same trick as lazygit/yazi).
- Startup: `gitpanel` inside any linked worktree discovers its siblings via `git rev-parse --git-common-dir`.

## Undo / redo (nice-to-have, infra built early)
- All mutating backend calls run inside `journal.mutation(op_name)`, which snapshots before/after: `HEAD` (symbolic or detached), all changed refs (`for-each-ref` diff), and — for destructive working-tree ops (rollback, reset --hard, discard, worktree remove --force) — a safety snapshot via `git stash create` (+ untracked files tree) stored as a ref under `refs/gitpanel/backup/<n>` so it isn't GC'd.
- `z` undo / `Z` redo restore the recorded ref states (`update-ref`, re-checkout) and, where present, re-apply the safety snapshot. This covers commit/amend/reset/rebase/cherry-pick/merge, branch delete/rename, checkout, and discards — discards being something lazygit's reflog-based undo can't do.
- Refuse with an explanation when unsafe: pushed commits (warn, offer revert), working tree dirty in conflicting paths, op in progress. Journal is per worktree; old backup refs pruned after N entries / days (configurable).

## Later features (milestone 10)
- **Patch builder** (lazygit's custom patch, done PyCharm-style): from a commit's diff, select files/hunks/lines → *Move to new commit* (before/after), *Move to commit…* (pick target in Log), *Remove from commit*, *Apply to working tree*, *Apply reversed*, *Shelve*. Implemented as `Patch(Commit(sha))` + `scripted_rebase`.
- **Bisect**: start from Log (mark good/bad on selected commits), skip, reset, view bisect log, `bisect run <cmd>` with output pane; markers on Log rows, banner shows remaining steps; candidate range highlighted.
- **Custom commands** in `~/.config/gitpanel/config.toml` (and per-repo `.gitpanel.toml`, opt-in trust):
  ```toml
  [[commands]]
  id = "open-pr"
  label = "Open PR for branch"
  key = "ctrl+o"
  context = "branch"                 # commit | branch | file | worktree | global
  run = "gh pr view --web {branch}"
  output = "none"                    # none | popup | terminal (suspend app)
  confirm = false
  prompts = [{ name = "msg", label = "Message" }]   # available as {msg}
  ```
  Appear in palette and context menus; mutating ones wrapped in `journal.mutation` so undo works.

## Cherry-picking between branches (priority workflow)
1. **From Log**: filter graph to any branch(es); multi-select commits on any branch → "Cherry-pick" applies them onto the current branch in topological order (`git cherry-pick A B C`), options: `-x` (record origin), `--no-commit` (stage into commit window/changelist), `-m N` for merge commits (prompt for parent).
2. **Already-picked detection**: like PyCharm's greyed rows — in "Compare with current" and log view, mark commits whose patch-id already exists on the current branch (`git log --cherry-mark --left-right current...other`), and exclude them from selection by default.
3. **Compare branches screen** (from Branches popup): two lists "in X not in current" / "in current not in X", each with diff; cherry-pick from either list.
4. **Cherry-pick to another branch without checkout**: run in a temporary `git worktree add` on the target, pick, then remove the worktree; on conflict, offer to switch to that worktree path or abort.
5. **Conflicts**: on stop, banner shows "Cherry-pick in progress (2/5)"; resolver opens; Continue / Skip / Abort map to `cherry-pick --continue|--skip|--abort`.
6. Patch-level pick: from a commit's changed-files tree, "Get from revision" / "Apply selected changes" for individual files or hunks (`git diff A^ A -- file | git apply --3way`).

## Milestones (each ends with tests passing + push)
1. Skeleton, runner, **journal `mutation()` recording**, active-worktree app state, **action registry + selection context**, **OperationState + banner**, status, diff parser + **source-agnostic `patch.py`**, Commit tool window with file + hunk staging, commit/amend.
2. Log: paging, graph lanes, **row-marker providers**, filters, details/diff, reset/revert/checkout revision; **`scripted_rebase` primitive** (used first for reword-from-Log).
3. Branches popup + **Worktrees tool window and all worktree integrations** + remotes (fetch/pull/push dialog), tags, `--cd-file`.
4. Cherry-pick suite (all six items above, worktree-backed cross-branch pick) + operation-in-progress banner.
5. Conflict resolver (3-pane), merge/rebase flows.
6. Interactive rebase editor, reword/fixup/squash/drop from Log.
7. History, selection history, annotate; shelf + stash; changelists persistence.
8. Undo/redo UI (`z`/`Z`, journal viewer listing past operations), backup-ref pruning.
9. Polish: keymap help (`?`), command palette entries, config file (`~/.config/gitpanel/config.toml`: theme, side-by-side default, confirmations, worktree path template, `[keys]` remapping), README with screenshots.
10. Patch builder, bisect, custom commands (see "Later features").

## Verification
- `pixi run test` — backend tests run against throwaway repos built by fixtures (incl. bare "origin" remote, divergent branches, conflict scenarios, merge commits for `-m`).
- Textual pilot tests: drive keys in `app.run_test()` and assert repo state (e.g. stage one hunk → `git diff --cached` contains only it; cherry-pick 2 commits from branch → `git log` on main shows them with `-x` trailer; create worktree for branch → switch → commit there → main worktree's log shows it; undo after hard reset and after a discard restores refs and file contents).
- Manual: `scripts/make_demo_repo.sh` builds a demo repo with branches, already-picked commits and a pending conflict; run `pixi run start demo-repo` and walk each tool window; snapshot screenshots with `textual-dev` for the README.
- CI: GitHub Actions using `prefix-dev/setup-pixi` (locked), running `pixi run lint` and `pixi run test`.
