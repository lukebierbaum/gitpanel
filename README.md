# gitpanel

**PyCharm's Git tooling, in your terminal.**

gitpanel is a keyboard-first Git client for the terminal, modelled on the Git integration in JetBrains IDEs: a commit window with hunk- and line-level staging, a log with a branch graph and a branches panel, worktrees, cherry-picking that knows what you've already picked, a 3-pane conflict resolver, interactive rebase, shelves — and **undo for everything**, including discarded changes.

![The Log: branch graph, branches panel, commit details](docs/screenshots/log.svg)

| Commit window | Compare branches |
|---|---|
| ![Commit window](docs/screenshots/commit.svg) | ![Compare branches](docs/screenshots/compare.svg) |
| **Branches popup** | **3-pane conflict resolver** |
| ![Branches popup](docs/screenshots/branches.svg) | ![Conflict resolver](docs/screenshots/resolver.svg) |
| **Worktrees** | **Git console** |
| ![Worktrees](docs/screenshots/worktrees.svg) | ![Console](docs/screenshots/console.svg) |

## Highlights

- **Stage exactly what you mean** — whole files, single hunks or individual lines, in unified or side-by-side diffs. Changelists, amend, sign-off, commit-and-push.
- **A log you can navigate** — coloured branch graph, a branches panel with folders (`feature/…`, `release/…`) to show any branch's history, filters by text, author, path and date, and dots that show what's **not pushed** (amber) or **only on the remote** (purple).
- **Cherry-picking between branches** — pick one or many commits, see which commits are **already picked** (by patch content or `-x` trailer), compare branches side by side, pick onto a branch you don't have checked out, or pick just a hunk or a file.
- **Worktrees as a first-class citizen** — create, switch, lock, move and remove them from a dedicated window; checking out a branch that lives in another worktree takes you there.
- **Conflicts without leaving the terminal** — yours │ result │ theirs, per-chunk accept / combine / edit, then continue the merge, rebase or cherry-pick.
- **History editing** — interactive rebase editor, reword, squash, fixup, drop, and a patch builder that moves hunks between commits.
- **Undo everything** — `z` undoes the last operation (commits, resets, rebases, deleted branches, even discarded changes); `alt+z` shows the full operation history.
- **See what it does** — the Console shows every git command gitpanel runs and git's reply.
- **Make it yours** — remap any key, add your own commands, pick a theme (default: *Hulk*, purple and gamma green).

## Install

Requires **Python 3.11+** and **git 2.45+**, on Linux or macOS.

```bash
pipx install git+https://github.com/lukebierbaum/gitpanel.git
```

Then, inside any repository or worktree:

```bash
gitpanel            # or: gitpanel path/to/repo
```

### Land in the worktree you were using

gitpanel can switch worktrees. Add this to your `~/.bashrc` (or `~/.zshrc`) and launch with `gp` to have your shell `cd` into the worktree that was active when you quit:

```bash
gp() {
  local f; f=$(mktemp)
  gitpanel --cd-file "$f" "$@"
  [ -s "$f" ] && cd "$(cat "$f")"
  rm -f "$f"
}
```

## Getting around

| Key | |
|---|---|
| `1` – `6` | tool windows: **Commit · Log · Worktrees · Compare · Shelf · Console** |
| `b` | Branches popup |
| `?` | every action and its key (filterable) |
| `ctrl+p` | command palette |
| `alt+enter` or `.` | actions for whatever is selected |
| `tab` / `shift+tab` | move between panes |
| `z` / `Z` | undo / redo — the hint bar shows what |
| `q` | quit |

Panes can be resized by dragging the thin dividers between them (double-click resets). In the key hints, `^` means Ctrl.

## Workflows

### Commit
| Key | |
|---|---|
| `space` | stage / unstage the file, group, hunk under the cursor, or selected lines |
| `v`, `shift+↑↓` | select lines in the diff, then `space` |
| `s` | toggle side-by-side diff |
| `n` / `p` | next / previous change |
| `a` / `A` | stage all / unstage all |
| `ctrl+k` | commit — `alt+m` amend, `alt+↑↓` message history |
| `ctrl+alt+k` | commit and push |
| `ctrl+alt+z` | roll back a file, hunk or lines (undoable) |
| `f6` | move files to another changelist |
| `alt+s` / `alt+t` | shelve the selection / stash everything |

### Log and branches
| Key | |
|---|---|
| branches panel | highlight a branch to see its history; `space` combines several; `→`/`←` open folders; `g` folders on/off; `[` hide the panel |
| `/` | filter by message (regex); branch, user, path and since filters alongside |
| `shift+↑↓` | select several commits |
| `R` · `S` · `D` · `alt+f` | reword · squash · drop · fold staged changes into a commit |
| `I` | interactive rebase from here (`p r e s f d` to set actions, `alt+↑↓` to reorder) |
| `B` · `T` · `W` | new branch · tag · worktree at a commit |
| `alt+h` / `alt+a` | file history / annotate (blame) |
| `F` · `ctrl+t` · `P` | fetch · update project (merge or rebase) · push dialog |

### Cherry-picking
| Key | |
|---|---|
| `C` | cherry-pick the selected commits onto the current branch (already-picked ones are skipped) |
| `alt+c` | … with options (`-x`, no-commit) |
| `ctrl+alt+c` | cherry-pick onto another branch without checking it out |
| `A` / `G` | apply the selected hunk from a commit / get the whole file from it |
| `b` → `ctrl+e` | Compare window: what's only on either branch, already-picked commits marked ✓ |

### Worktrees (`3`)
`n` new · `enter` switch · `d` remove · `m` move · `l` lock · `s` open a shell there · `y` copy path. In the Branches popup, `ctrl+w` makes a worktree for a branch.

### Conflicts
Conflicted files appear under **Merge Conflicts** in the Commit window, and a banner offers Continue / Skip / Abort. `enter` opens the resolver: `←`/`→` take yours/theirs for the current chunk, `b`/`B` take both, `e` edit, `ctrl+s` save and mark resolved.

### More
Patch builder (on a selection in a commit's diff: `alt+r` remove it from the commit, `alt+n` move it to a new commit, `alt+m` move it to another commit), bisect (`alt+b` / `alt+g` mark bad / good), Smart and Force Checkout when local changes are in the way, and an operation history with undo-to-here (`alt+z`).

## Configuration

`gitpanel --init-config` writes a commented `~/.config/gitpanel/config.toml`:

```toml
theme = "hulk"                 # hulk, hulk-light, or any Textual theme

[diff]
side_by_side = true            # start diffs side-by-side
context = 3

[log]
branch_folders = true          # group feature/x, release/y … in the branches panel

[worktrees]
path_template = "../{repo}.worktrees/{branch}"

[confirm]
destructive = true             # ask before discarding work (it's always backed up)

[keys]                         # remap by action id (see `?`); "a,b" for several keys, "" to unbind
"commit.commit" = "ctrl+s"
```

### Custom commands

Add your own commands; they appear in `?`, the palette and the actions menu:

```toml
[[commands]]
id = "open-pr"
label = "Open pull request"
key = "ctrl+o"
context = "branch"             # global | commit | branch | file | worktree
run = "gh pr view --web {branch}"
output = "none"                # none | popup | terminal
read_only = true               # commands that change the repo are recorded for undo
```

Placeholders — `{repo} {worktree} {commit} {commits} {short} {branch} {ref} {file} {files} {count}` and any `prompts` you define — are shell-quoted. Other braces (`{ print $1 }`, `${VAR}`) are left alone, so multi-line shell snippets work as written.

## Large repositories

On big histories, git answers questions like "which branches contain this commit" much faster with a **commit-graph** — a small local cache (about 60 bytes per commit) that never leaves your machine. Turn it on for every repository:

```bash
git config --global fetch.writeCommitGraph true
```

## How it compares

|  | lazygit | gitui | tig | **gitpanel** |
|---|---|---|---|---|
| Hunk / line staging | ✅ | ✅ | ✅ | ✅ |
| Branch graph, history per branch | ✅ | ✗ | ◐ | ✅ |
| Already-picked commit detection | ✗ | ✗ | ✗ | ✅ |
| Cherry-pick onto a branch you're not on | ✗ | ✗ | ✗ | ✅ |
| 3-pane conflict resolver | ◐ | ✗ | ◐ | ✅ |
| Worktree management | ✅ | ✗ | ✗ | ✅ |
| Undo, including discarded changes | ◐ | ✗ | ✗ | ✅ |
| Shelves and changelists | ✗ | ✗ | ✗ | ✅ |
| Command log / console | ✅ | ✗ | ✗ | ✅ |

gitpanel drives the real `git` CLI, so hooks, config, credential helpers and LFS behave exactly as they do on the command line. Feature-by-feature notes against PyCharm are in [docs/PARITY.md](docs/PARITY.md).

## Development

Uses [pixi](https://pixi.sh) for the environment.

```bash
pixi run demo /tmp/gp-demo      # build a demo repository
pixi run start /tmp/gp-demo     # run gitpanel on it
pixi run test                   # ~180 tests against real git repositories
pixi run lint
pixi run python scripts/screenshots.py   # regenerate docs/screenshots
```

Built with [Textual](https://textual.textualize.io). Design notes: [architecture](docs/architecture.md) · [roadmap](docs/ROADMAP.md) · [PyCharm parity](docs/PARITY.md) · [contributor guide](CLAUDE.md).

## License

MIT — see [LICENSE](LICENSE).
