# gitpanel

A PyCharm-style Git client for the terminal: commit tool window with hunk/line staging, log with branch graph, branches popup, worktrees, cherry-picking between branches (with already-picked detection), 3-pane conflict resolver, history/annotate, shelves, interactive rebase — and undo for all of it, including discarded changes.

![Commit window](docs/screenshots/commit.svg)

| | |
|---|---|
| ![Log](docs/screenshots/log.svg) | ![Compare branches](docs/screenshots/compare.svg) |
| ![Branches popup](docs/screenshots/branches.svg) | ![Conflict resolver](docs/screenshots/resolver.svg) |

**Status:** all 10 milestones of the [roadmap](docs/ROADMAP.md) are implemented.

## Install
```bash
pipx install git+ssh://git@github.com/lukebierbaum/gitpanel.git
gitpanel            # in any repository or worktree
gitpanel --init-config   # writes ~/.config/gitpanel/config.toml with every option
```

Or from a checkout with [pixi](https://pixi.sh):
```bash
pixi run demo /tmp/gp-demo      # build a demo repository
pixi run start /tmp/gp-demo     # open it
```

Press `?` anywhere for every action and its key, `ctrl+p` for the command palette, `alt+enter` for actions on the current selection.

### Commit tool window
| Key | Action |
|---|---|
| `↑↓` / `j k`, `tab` | move; tab cycles changes → diff → message |
| `space` | stage / unstage the file, group, hunk under the cursor, or selected lines |
| `v`, `shift+↑↓` | select lines in the diff (then `space`) |
| `n` / `p` (`F7` / `shift+F7`) | next / previous change |
| `a` / `A` | stage all / unstage all |
| `ctrl+k` | commit (`alt+m` toggles amend; `alt+↑↓` message history) |
| `ctrl+alt+z` | rollback file / hunk / lines (undoable) |
| `z` / `Z` | undo / redo the last operation, including discarded changes (the hint shows what) |
| `alt+z` | operation history — `enter` undoes (or redoes) everything down to the selected entry |
| `ctrl+p` | command palette (everything available in the current context) |
| `q` | quit |

### Log (`alt+9`)
| Key | Action |
|---|---|
| `/` | filter by message text/regex (branch, user, path, since fields alongside) |
| `shift+↑↓` | select several commits |
| `R` | edit commit message (any commit on the current branch) |
| `B` / `T` | new branch / tag at the commit |
| `y` | copy hash |
| `I` | interactively rebase from here (editor: `p r e s f d`, `alt+↑↓` move, `ctrl+s` start) |
| `S` / `D` | squash / drop the selected commits |
| `alt+f` | fold the staged changes into the selected commit (fixup + autosquash) |
| `ctrl+p` | everything else: checkout revision, reset to here, revert… |
| `s` (in a diff) | side-by-side diff |

### Cherry-picking
| Key | Action |
|---|---|
| `C` | cherry-pick the selected commits onto the current branch (already-picked ones are skipped) |
| `alt+c` | … with options (`-x`, no-commit) |
| `ctrl+alt+c` | cherry-pick onto another branch without checking it out |
| `A` (in a commit's diff) / `G` (on a commit's file) | apply the selected hunk/lines / get the whole file from that revision |
| `b` → `ctrl+e` | Compare window: commits only in either branch, already-picked ones marked ✓ (`h` hides them, `x` swaps) |

Commits whose change already exists on your branch are dimmed and tagged `✓ picked` in the Log.

### History, annotate, shelf, stash, changelists
| Key | Action |
|---|---|
| `alt+h` | file history (Commit window, Log, history) — or history of the selected lines in Annotate |
| `alt+a` | annotate (git blame); in Annotate: `p` previous revision, `enter` show commit in Log, `v` select lines |
| `alt+s` / `alt+t` | shelve the selected changes / stash all changes |
| `alt+5` | Shelf window: shelves and stashes — `enter` unshelve / pop, `a` apply, `d` delete/drop, `B` branch from stash |
| `f6` | move files to another changelist (new / rename / delete / set active via `alt+enter`) |

### Patch builder and bisect (Log / History, on a commit's diff selection)
| Key | Action |
|---|---|
| `alt+r` | remove the selected hunks/lines from that commit |
| `alt+n` | move them out into a new commit right after it |
| `alt+m` | move them into another commit on the branch |
| `alt+i` | move them out of the commit into the index |
| `alt+v` | revert them in the working tree |
| `alt+b` / `alt+g` / `alt+k` | bisect: mark the selected commit bad / good / skip (starts a bisect); `ctrl+p` → "Bisect: Run a Command…" automates it |

### Custom commands
```toml
[[commands]]
id = "open-pr"
label = "Open pull request"
key = "ctrl+o"
context = "branch"            # global | commit | branch | file | worktree
run = "gh pr view --web {branch}"
output = "none"               # none | popup | terminal
read_only = true              # false (default) records it in the undo journal
# confirm = "Really?"   prompts = [{ name = "msg", label = "Message" }]  → {msg}
```
Placeholders: `{repo} {worktree} {commit} {commits} {short} {branch} {ref} {file} {files} {count}` and your prompts — all shell-quoted. They show up in `?`, the palette and `alt+enter`.

### Merge conflicts
Conflicted files appear under **Merge Conflicts** in the Commit window and the banner offers Continue / Skip / Abort.

| Key | Action |
|---|---|
| `enter` | open the 3-pane resolver (yours │ result │ theirs) |
| `←` / `→` | take yours / theirs for the current chunk (jumps to the next unresolved one) |
| `b` / `B` | take both (yours first / theirs first) |
| `e` / `u` | edit the result by hand / reset the chunk |
| `n` / `p` | next / previous conflict |
| `ctrl+s` | save and mark resolved |
| `alt+←` / `alt+→` (in the Commit window) | accept yours / theirs for the whole file |

### Branches, worktrees, remotes
| Key | Action |
|---|---|
| `b` | Branches popup — type to search; `enter` checkout, `ctrl+n` new, `ctrl+k` push, `f2` rename, `ctrl+d` delete, `ctrl+f` favourite, `ctrl+w` new worktree, `alt+enter` all actions |
| `w` / `alt+8` | Worktrees window — `enter` switch, `n` new, `d` remove, `m` move, `l` lock, `s` shell, `y` copy path |
| `F` / `ctrl+t` / `P` | fetch / update project (pull, merge or rebase) / push dialog |
| `alt+enter` | actions for the current selection, anywhere |

Colour scheme: **Hulk** (purple + gamma green) by default; `hulk-light` or any Textual theme via `theme = "…"` in the config.

### Configuration
`gitpanel --init-config` writes a commented `~/.config/gitpanel/config.toml`: theme, side-by-side diffs, diff context, worktree path template, journal size, whether to confirm destructive actions, and `[keys]` to remap any action by id (ids are listed in `?`). A repository's `.gitpanel.toml` is only read with `--trust-repo-config`.

### `cd` into the worktree you were in when you quit
```bash
gp() {
  local f; f=$(mktemp)
  gitpanel --cd-file "$f" "$@"
  [ -s "$f" ] && cd "$(cat "$f")"
  rm -f "$f"
}
```

## Development
```bash
pixi run test
pixi run lint
pixi run python scripts/screenshots.py   # regenerate docs/screenshots
```

Design docs: [architecture](docs/architecture.md) · [milestones](docs/milestones/) · [contributor rules](CLAUDE.md)
