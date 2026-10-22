# gitpanel

A PyCharm-style Git client for the terminal: commit tool window with hunk/line staging, log with branch graph, branches popup, worktrees, cherry-picking between branches, 3-pane conflict resolver, history/annotate, shelves, interactive rebase, and undo.

**Status:** milestones 1–5 of 10 done (commit window, log with graph, branches/worktrees/remotes, cherry-picking, conflict resolver). See [docs/ROADMAP.md](docs/ROADMAP.md).

## Try it
Requires [pixi](https://pixi.sh).

```bash
pixi run demo /tmp/gp-demo      # build a demo repository
pixi run start /tmp/gp-demo     # open it
```

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
| `z` / `Z` | undo / redo the last operation, including discarded changes |
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

Colour scheme: **Hulk** (purple + gamma green) by default; `hulk-light` is available from the palette's theme switcher.

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
```

Design docs: [architecture](docs/architecture.md) · [milestones](docs/milestones/) · [contributor rules](CLAUDE.md)
