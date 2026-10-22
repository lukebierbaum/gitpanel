# gitpanel

PyCharm-style Git client for the terminal (Python 3.11+, Textual). Shells out to the `git` CLI.

- What and when: [docs/ROADMAP.md](docs/ROADMAP.md)
- Contracts every feature builds on: [docs/architecture.md](docs/architecture.md) — **read before changing anything in `gitpanel/git/session.py`, `journal.py`, `patch.py`, `operations.py`, `scripted_rebase.py`, `markers.py` or `gitpanel/actions/`**
- Current milestone spec: [docs/milestones/](docs/milestones/) (highest-numbered file)

## Commands (always via pixi)
```bash
pixi run start [PATH]      # run the app
pixi run test              # pytest
pixi run lint              # ruff check
pixi run fmt               # ruff format
pixi run dev               # textual dev console
pixi run demo DEST         # build a demo repo to try things in
```
Add dependencies with `pixi add <pkg>` (conda-forge) or `pixi add --pypi <pkg>` — never `pip install`. Runtime deps also go in `[project].dependencies`. Commit `pixi.lock` changes.

## Rules
1. **Layering:** `gitpanel.ui → gitpanel.actions → gitpanel.git`. `gitpanel.git` never imports Textual or `gitpanel.actions`/`ui`.
2. **All git access goes through `Git.run()/stream()`** — no `subprocess` elsewhere, no GitPython/pygit2. Use `-z`/NUL formats and `read_only=True` for queries.
3. **Every repo mutation runs inside `journal.mutation()`** — normally automatic via the `Dispatcher` (`Action(mutating=True)`). Ops that discard working-tree content set `destructive=True`. Each mutating action needs an undo test.
4. **Screens don't bind git operations.** Register an `Action` in `gitpanel/actions/builtin/<area>.py` with scopes/`when`/key; screens only publish `SelectionContext`. No git keys in Textual `BINDINGS`.
5. **Actions talk to the user only via the `Ui` protocol** (confirm/prompt/choose/notify/show_error/show_output/run_in_terminal/post) so they're testable with `FakeUi`.
6. **Hunk/line selection uses `patch.py`** (`DiffSource` + `Selection` + `build_patch` + `apply`) — never hand-roll patch text.
7. **Rewriting history uses `scripted_rebase.run()`**, inside one mutation, so it's one undo step.
8. **Log decorations are `MarkerProvider`s**; don't special-case them in the graph renderer.
9. **Refresh on `ChangeKind` events** from the session; no per-screen polling.
10. gitpanel's own state lives in `session.private_dir()` (`<git-dir>/gitpanel` per worktree, `<common-dir>/gitpanel` shared) and refs under `refs/gitpanel/`. Never write elsewhere in the repo.
11. Changing a contract in architecture.md → update the doc in the same commit.

## Tests
- Use the `repo_factory` fixture (real git in tmp dirs, deterministic author/dates). Don't mock git.
- Backend logic → `tests/git/`; actions with `FakeUi` → `tests/actions/`; a few behavioural Textual pilot tests → `tests/ui/`.
- Run `pixi run lint && pixi run test` before committing.

## Conventions
- Async everywhere in backend (`async def`), dataclasses (frozen where practical) for models, `StrEnum`/`Flag` for enums.
- Type hints on all public functions; ruff config in `pyproject.toml` (line length 100).
- Action ids: `<area>.<verb>` (`log.cherry_pick`, `worktree.add`); custom commands `custom.<id>`.
- Widget bindings that only sometimes apply (e.g. `escape` clearing a selection) raise `textual.actions.SkipAction` when there's nothing to do, so the key reaches the screen.
- Key defaults follow PyCharm where the terminal allows (e.g. `ctrl+k` commit); document deviations next to the action.
