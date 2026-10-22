# Feature parity with PyCharm's Git tooling

✅ done · ◐ partial · ✗ missing · ➕ gitpanel goes beyond PyCharm. Checked against the code on 2026-10-22; IDE-only features (editor gutter markers, code inspections, local history) are out of scope.

## Commit
| PyCharm | gitpanel | Notes |
|---|---|---|
| Partial commits (hunks / lines) | ✅ | space on hunk, `v` for lines |
| Changelists, active changelist | ✅ | `f6` move; Staged/Unversioned stay separate groups |
| Amend, author, sign-off, message history | ✅ | |
| Commit and Push | ✅ | `ctrl+alt+k` |
| Rollback (file / hunk), add to .gitignore | ✅ | rollback is undoable ➕ |
| Commit message checks (subject length, blank 2nd line) | ✗ | |
| Before-commit checks (run tests / analyse) | ✗ | could be a configurable pre-commit command |
| AI-generated commit message | ✗ | could call Claude on the staged diff |
| Commit message template (`commit.template`) | ✗ | |

## Diff viewer
| Unified / side-by-side | ✅ | `s`, config default |
|---|---|---|
| Word-level (inline) change highlighting | ✗ | PyCharm highlights the changed words inside a line |
| Syntax highlighting | ✗ | |
| Ignore whitespace / blank lines | ✗ | |
| Show diff with working tree (branch / commit vs local) | ✗ | |

## Log
| Graph, filters (branch / user / date / path / text) | ✅ | until-date and hash filter boxes missing |
|---|---|---|
| Branches panel beside the log | ✅ | folders, multi-select ➕ |
| Details, changed files, per-file diff | ✅ | |
| Checkout revision, new branch/tag, reset, revert, reword, squash, fixup, drop, interactive rebase | ✅ | |
| Cherry-pick (multi), already-picked marks | ✅ | pick onto a branch you're not on ➕ |
| Go to hash / branch / tag | ◐ | branch via filter; no "go to hash" jump |
| Show only first parent / hide merges / IntelliSort | ✗ | backend supports first-parent / no-merges |
| Highlight my commits / not merged to current | ✗ | |
| Show repository at revision (browse files) | ✗ | |
| Push all up to here | ✗ | |
| Create patch from commits | ✗ | |
| Open commit on GitHub | ✗ | doable as a custom command today |
| Pushed / not pushed / remote-only commits | ✅ | graph dots: amber = not pushed, purple = remote only; details list containing remote branches |

## Branches, remotes, update, push
| Branches popup: recent / local / remote / tags, favourites, search | ✅ | |
|---|---|---|
| Checkout, Smart / Force Checkout, checkout & rebase, merge, rebase, pull into current, push, rename, delete (undoable), upstream | ✅ | |
| Compare with current | ✅ | commits view; PyCharm also has a **Files** tab (combined diff) — ✗ |
| Merge / rebase dialogs with options (`--no-ff`, `--squash`, `--onto`, choose upstream) | ✗ | |
| Update Project (merge / rebase, auto-stash) | ✅ | asks each time; no saved default |
| Fetch, background auto-fetch | ◐ | manual only |
| Push dialog (outgoing commits, force-with-lease, tags, set upstream) | ✅ | protected branches ✗ |
| Manage remotes (add / rename / remove / URL) | ✗ | backend exists, no UI |
| GitHub pull requests (list, checkout, review, create) | ✗ | could use `gh` |

## Stash, shelf, patches
| Shelve / unshelve (partial), stash push/apply/pop/drop/branch | ✅ | |
|---|---|---|
| Stash clear, unstash "reinstate index" choice | ◐ | |
| Recently deleted shelves | ✗ | |
| Create patch / apply patch (file or clipboard) | ✗ | |

## History and annotate
| File history across renames, history for selection | ✅ | |
|---|---|---|
| Folder history | ◐ | uses `--follow`, meant for single files — needs checking |
| Annotate, previous revision, show commit | ✅ | |
| Annotate options (ignore whitespace, detect moves/copies, colour by author) | ✗ | |
| Compare file with branch / revision | ✗ | |

## Conflicts and rebases
| Conflict list, accept yours/theirs, 3-pane merge with per-chunk actions | ✅ | |
|---|---|---|
| Base pane, ignore whitespace | ✗ | |
| "Resolve All Simple Conflicts" (2026.2) | ◐ | non-overlapping edits auto-merge; whitespace/import-only conflicts don't |
| Continue / skip / abort for merge, rebase, cherry-pick, revert | ✅ | plus bisect ➕ |
| Interactive rebase editor | ✅ | rebasing ranges with merges ✗ |

## Other
| Console (commands + output) | ✅ | `6` |
|---|---|---|
| Worktrees | ✅ ➕ | full window, switching, worktree-aware checkout |
| Undo for every operation incl. discards | ➕ | `z`, `alt+z` history |
| Bisect, patch builder, custom commands | ➕ | |
| Submodules | ✗ | |
