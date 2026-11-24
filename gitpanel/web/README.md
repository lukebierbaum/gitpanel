# gitpanel web UI (prototype)

A second front end for gitpanel: the same backend (`git`, `forge`, `actions`) behind a page
you open in a browser or in a desktop window instead of the terminal.

```bash
pixi run web PATH          # serve it and open your browser (gitpanel --web)
pixi run -e app app PATH   # the same page in its own desktop window (gitpanel --app)
```

`--web` takes `--port N` and `--no-browser`. The server listens on 127.0.0.1 only and prints a
link with a per-run token; the page and the WebSocket refuse requests without it, and the
WebSocket also refuses pages from any other origin.

## What works

- **Log**: SVG commit graph, ref badges (branches, remotes, tags, worktrees, HEAD),
  unpushed/incoming markers, filters (text, author, paths, dates), multi-select, paging,
  commit details, changed files and a diff with intra-line highlighting.
- **Changes**: conflict / staged / unstaged / untracked groups, stage / unstage / roll back a
  file, a hunk or selected lines; commit, amend, sign-off, Commit and Push.
- **Everywhere**: branch sidebar (filter the log, check out, right-click menu), context menus
  built from the action registry, `ctrl+P` action palette, the TUI's key bindings and the
  user's `[keys]` / `[[commands]]`, undo/redo, Fetch/Pull/Push buttons, an operation banner
  (rebase/merge in progress, continue/abort), live refresh on repo changes, light/dark from
  the system setting.

## How it fits together

| File | Role |
| --- | --- |
| `server.py` | aiohttp app; one `RepoSession` shared by every tab, one WebSocket per tab; `Server.start()/stop()` |
| `api.py` | `Connection`: JSON calls (`log.page`, `diff.file`, `actions.run` …); rebuilds `SelectionContext` from what the browser names (shas, ref names, paths, hunk/line indexes) |
| `bridge.py` | `WebUi`, the `Ui` protocol over the socket: every dialog is an `ask` the page `answer`s |
| `serialize.py` | git models → plain JSON for the page |
| `window.py` | `--app`: pywebview window, server on a background-thread event loop |
| `static/` | plain ES modules and CSS, no build step |

Actions run unmodified: anything that works through the registry and the `Ui` protocol
works here. The graph is drawn from `GraphRow.edges` (see `git/graph.py`). Contracts are in
[docs/architecture.md](../../docs/architecture.md).

Tests: `tests/web/test_web.py` drives a real server against throwaway repos (no browser).

## TODO

### Missing screens
Actions that open these show "isn't in the web prototype yet" (see `open_screen` in
`static/js/main.js`). The TUI versions live in `gitpanel/ui/screens/`.

- [ ] Conflict resolver (`resolver`): three-way merge per file; the op banner already offers continue/abort
- [ ] Interactive rebase editor (`rebase`): reorder / squash / fixup / edit / drop
- [ ] Branch comparison (`compare`)
- [ ] Worktrees (`worktrees`)
- [ ] Shelf / stashes (`shelf`)
- [ ] Pull requests (`prs`, via `gitpanel.forge`)
- [ ] File history (`history`) and blame (`annotate`)
- [ ] Console (`console`): commands gitpanel ran, from `git.console.CONSOLE`
- [ ] Undo journal browser (`journal`)
- [ ] Help / key reference (`help`); the branch picker currently stands in for `branches`

### Gaps in the existing views
- [ ] Changelists in the Changes view (`api.py` already loads them; the page doesn't show them)
- [ ] Show renames/copies (old → new path) in file lists; `orig_path` is sent but not displayed
- [ ] Diffs over 4000 lines are cut (`MAX_DIFF_LINES`); add "show the rest"
- [ ] Commit list keeps every loaded row in the DOM; virtualize for very large logs
- [ ] Side-by-side diff option
- [ ] Theme switcher; the CSS palette mirrors `ui/colors.py` by hand and can drift

### Processes and credentials
- [ ] `WebUi.run_in_terminal` has no stdin: push/pull that needs a password fails fast instead
      of prompting. Options: a credential dialog via `GIT_ASKPASS`, or a real terminal (pty + xterm.js)
- [ ] Report those processes to `CONSOLE` so they show in a future console view

### Desktop app
- [ ] Layout preferences (`localStorage`) are lost between runs because the port, and so the
      origin, changes; store them in gitpanel's config or `session.private_dir()` instead
- [ ] Repository picker / recent repos, for an app started without a path
- [ ] App icon, native menus
- [ ] Packaging (Briefcase or PyInstaller): `.app`, AppImage/`.deb`, Windows installer, with git bundled or required
- [ ] Test `--app` on macOS (the pixi lock solves, but it has never been run there)

### Quality
- [ ] Browser-level tests (Playwright) for the page itself; today only the server is tested
- [ ] Accessibility pass: focus order, ARIA roles for lists and dialogs, contrast in light mode
- [ ] Several repos / worktree switching from one server
