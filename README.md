# gitpanel

A PyCharm-style Git client for the terminal: commit tool window with hunk/line staging, log with branch graph, branches popup, worktrees, cherry-picking between branches, 3-pane conflict resolver, history/annotate, shelves, interactive rebase, and undo.

**Status:** milestones 1–2 of 10 done (commit tool window, log with graph). See [docs/ROADMAP.md](docs/ROADMAP.md).

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
