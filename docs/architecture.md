# gitpanel architecture

This document fixes the **contracts** of the foundations every feature builds on. Signatures here are the target API; implementation details may vary, but changing a contract means updating this file in the same PR.

Read with: [ROADMAP.md](ROADMAP.md) (what and when), [../CLAUDE.md](../CLAUDE.md) (working rules).

## 1. Layers

```
┌──────────────────────────── gitpanel.ui ────────────────────────────┐
│ Textual screens & widgets. Render state, publish SelectionContext,  │
│ never call git directly, never bind git operations to keys.         │
└───────────────▲──────────────────────────────────┬──────────────────┘
                │ ChangeKind events                │ dispatch(action_id | key)
┌───────────────┴────────── gitpanel.actions ──────▼──────────────────┐
│ Registry of Actions (built-in + custom). Dispatcher wraps mutating  │
│ actions in journal.mutation(). Talks to the UI only via `Ui` proto. │
└───────────────▲──────────────────────────────────┬──────────────────┘
                │                                  │
┌───────────────┴─────────── gitpanel.git ─────────▼──────────────────┐
│ Pure async backend: RepoSession, Git runner, parsers, Journal,      │
│ Patch, ScriptedRebase, OperationState, MarkerProviders. No Textual. │
└─────────────────────────────────────────────────────────────────────┘
                                   │ subprocess
                                  git
```

Import rule: `ui → actions → git`. Never the reverse. `gitpanel.git` must be importable and testable without Textual installed.

## 2. Package layout

```
gitpanel/
  __main__.py                 # CLI entry: gitpanel [PATH] [--cd-file FILE] [log|branches|commit|worktrees]
  config.py                   # load ~/.config/gitpanel/config.toml (+ opt-in per-repo .gitpanel.toml)
  git/
    runner.py                 # Git, GitResult, GitError (every call reports to console.CONSOLE)
    console.py                # Console/ConsoleEntry: recorded commands + output for the Console window
  forge/                      # code-hosting integrations (no Textual), used by actions/prs.py
    runner.py                 # Gh: runs `gh`, explains errors (ForgeError), reports to CONSOLE (kind "gh")
    models.py                 # PullRequest, Check, Review, Comment, ReviewThread, PRStatus, StackInfo
    github.py                 # GitHub provider + local side (fetch pull/N/head → refs/gitpanel/pr/N);
                              # gh stack link / merge, retarget
    stacks.py                 # build_stacks: GitHub stacks, chained PRs, local stacks (pure)
    filters.py                # PR list filter (GitHub search syntax)
    session.py                # RepoSession, Worktree, ChangeKind
    models.py                 # shared dataclasses (Commit, Ref, FileStatus, StashEntry, …)
    journal.py                # Journal, JournalEntry, HeadState
    operations.py             # OperationState, OpKind, detect()
    patch.py                  # DiffSource, FileDiff, Hunk, DiffLine, Selection, build_patch(), apply()
    scripted_rebase.py        # TodoItem, run()
    stack.py                  # gh-stack's record (read-only), local chains, nearest_base,
                              # restack(): a whole stack in one scripted rebase (update-ref per layer)
    _editor.py                # GIT_EDITOR / GIT_SEQUENCE_EDITOR helper (python -m gitpanel.git._editor)
    markers.py                # Marker, MarkerProvider, built-in providers
    status.py  log.py  graph.py  refs.py  remote.py  commit.py  cherry_pick.py
    conflicts.py  history.py  stash.py  shelf.py  changelists.py  patch_builder.py
    reset_revert.py  tags.py  worktrees.py  bisect.py
  actions/
    registry.py               # Action, Registry, Dispatcher
    context.py                # SelectionContext, Scope
    ui_protocol.py            # Ui protocol (confirm/prompt/choose/notify/show_error/show_output/run_in_terminal/post)
    builtin/                  # one module per area: commit.py, log.py, branches.py, worktrees.py, …
    custom.py                 # [[commands]] from config → Actions (sh -c, shell-quoted values)
  ui/
    app.py                    # GitPanelApp: owns RepoSession, Registry, Dispatcher; implements Ui
    palette.py                # Textual CommandProvider over the registry
    theme.py                  # "hulk" (default) and "hulk-light" themes: purple primary, green accents
    base.py                   # ToolWindow / ToolScreen / GpMixin: the tool-window contract and its defaults
    fmt.py                    # dates, ages ("5h"), durations, plural(), ahead_behind(), branch_or_detached()
    colors.py                 # the palette (dark/light) — every colour the UI draws; C.<name> at render time
    widgets/                  # DiffView (unified + side-by-side), CommitTable, FileTree, OperationBanner, KeyHints,
                              # ListTable (every table-like list: filter bar, column sort, flexible column)
                              # FileList (a change's files beside a DiffView: labels, selection)
                              # TitleBar (header line: title · info · key reminders)
                              # CursorView (row cursor + range base for DiffView, CommitTable, annotate)
                              # branch_label (branch/tag rendering, favourites)
    screens/                  # commit, log, branches, worktrees, conflicts, history, annotate, shelf, rebase_editor, push, compare
    dialogs.py
tests/
  conftest.py                 # RepoFactory fixture
  git/  actions/  ui/
```

## 3. Git runner (`git/runner.py`)

```python
@dataclass(frozen=True)
class GitResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: str

    @property
    def text(self) -> str: ...  # stdout decoded utf-8 (surrogateescape)


class GitError(Exception):
    result: GitResult  # message = first stderr line


class Git:
    def __init__(self, cwd: Path, *, git_exe: str = "git") -> None: ...
    async def run(
        self,
        *args: str,
        input: bytes | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        read_only: bool = False,  # sets GIT_OPTIONAL_LOCKS=0
    ) -> GitResult: ...
    async def stream(
        self, *args: str, sep: bytes = b"\0"
    ) -> AsyncIterator[bytes]: ...  # log paging
    async def rev_parse(self, *args: str) -> str: ...
```

Rules:
- Base env: `LC_ALL=C`, `GIT_PAGER=cat`, `GIT_EDITOR` and `GIT_SEQUENCE_EDITOR` = `sys.executable -m gitpanel.git._editor` (which fails safely unless a scripted rebase has staged instructions, so git never blocks on an editor).
- Always pass `-c color.ui=false -c core.quotepath=false`; use `-z` and NUL-separated formats wherever git offers them.
- Network commands (fetch/pull/push) run with `GIT_TERMINAL_PROMPT=0` first; on auth failure the UI offers to rerun via `Ui.run_in_terminal()` in the real terminal so credential prompts work.
- Cancellation: cancelling the awaiting task kills the subprocess.
- A working folder that no longer exists (moved or deleted) fails like git does — a `GitError` (`fatal: cannot change to '<cwd>'`), not `FileNotFoundError` — so the usual GitError handling covers it.

## 4. Session, worktrees and change events (`git/session.py`)

```python
class ChangeKind(Flag):
    INDEX = auto()
    WORKTREE = auto()
    HEAD = auto()
    REFS = auto()
    STASH = auto()
    CONFIG = auto()
    OPERATION = auto()
    WORKTREES = auto()
    ALL = INDEX | WORKTREE | HEAD | REFS | STASH | CONFIG | OPERATION | WORKTREES


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str | None  # sha; None for unborn
    branch: str | None  # full ref, None if detached
    is_main: bool
    locked: str | None  # reason ("" if locked without reason)
    prunable: str | None


class RepoSession:
    common_dir: Path  # git rev-parse --git-common-dir
    active: Worktree
    git: Git  # bound to active.path
    journal: Journal  # per active worktree
    lock: asyncio.Lock  # serialises mutations

    @classmethod
    async def open(cls, path: Path) -> RepoSession: ...
    async def worktrees(self) -> list[Worktree]: ...
    async def switch(self, path: Path) -> None: ...  # re-binds git/journal, emits ChangeKind.ALL
    async def refresh_active(self) -> Worktree: ...
    # re-reads the active worktree; if it's unusable (folder moved/deleted, `.git` link stale)
    # it switches to another worktree that still exists (main first) like switch(), or
    # raises WorktreeGone when none is left (e.g. the whole repository was moved). The app
    # notifies on a fallback, reports WorktreeGone once and skips the refresh.
    def subscribe(self, cb: Callable[[ChangeKind], None]) -> Callable[[], None]: ...
    def emit(self, kinds: ChangeKind) -> None: ...
    def private_dir(self, *, shared: bool = False) -> Path:
        ...
        # shared=False → <git-dir>/gitpanel   (per worktree: journal, messages)
        # shared=True  → <common-dir>/gitpanel (repo-wide: shelf, changelists, favourites)
        # created on demand, but never re-creates a git dir that has been moved or deleted
```

External change detection: a watcher task polls mtimes of `index`, `HEAD`, `packed-refs`, `refs/` (recursive dir mtimes), `logs/HEAD`, operation marker files and `worktrees/` every 1 s while the app has focus, and emits the matching `ChangeKind`s. Screens refresh on events only — never on timers of their own.

## 5. Journal — undo/redo (`git/journal.py`)

```python
@dataclass(frozen=True)
class HeadState:
    ref: str | None  # "refs/heads/x" when symbolic
    sha: str | None


@dataclass
class JournalEntry:
    id: int
    op: str  # human label, e.g. "Cherry-pick 3 commits"
    time: datetime
    head_before: HeadState
    head_after: HeadState
    refs_before: dict[str, str | None]  # only refs that changed; None = didn't exist
    refs_after: dict[str, str | None]
    backup: str | None  # refs/gitpanel/backup/<id>: worktree+index snapshot before the op
    backup_after: str | None  # refs/gitpanel/backup/<id>-after: snapshot after it (for redo)
    paths: list[str] | None  # paths a destructive op touched (None = whole tree)
    reset_mode: ResetMode = "keep"  # soft | mixed | keep | hard — how the current branch is moved
    undone: bool = False
    failed: bool = False


class UndoRefused(Exception):
    reason: str


class Journal:
    @asynccontextmanager
    async def mutation(
        self,
        op: str,
        *,
        destructive: bool = False,
        paths: Sequence[str] | None = None,
        reset_mode: ResetMode = "keep",
    ) -> AsyncIterator[JournalEntry]: ...
    def can_undo(self) -> JournalEntry | None: ...
    def can_redo(self) -> JournalEntry | None: ...
    async def undo_to(self, entry_id: int) -> list[JournalEntry]: ...  # stops at a refusal
    async def redo_to(self, entry_id: int) -> list[JournalEntry]: ...
    async def undo(self) -> JournalEntry: ...  # raises UndoRefused
    async def redo(self) -> JournalEntry: ...
    def entries(self, limit: int = 100) -> list[JournalEntry]: ...
    async def prune(self, keep: int, max_age: timedelta) -> None: ...
```

Semantics:
- `mutation()` acquires `session.lock`, snapshots `HEAD` + all refs (`for-each-ref --format='%(refname) %(objectname)'`, excluding `refs/gitpanel/`), yields, snapshots again, stores only the diff, appends one JSON line to `<git-dir>/gitpanel/journal.jsonl`, then emits `ChangeKind`s derived from what changed (plus `INDEX|WORKTREE` always).
- **Nested** mutations collapse into the outermost entry (one user action = one undo step).
- **Failed** mutations (exception) are still recorded if any ref moved, labelled `"<op> (failed)"`.
- Mutations that moved no ref/HEAD and made no backup (e.g. staging) are not recorded.
- A new recorded mutation discards entries that were undone (linear undo/redo history).
- `destructive=True` first writes a **backup commit**: copy the index (with `shutil.copy2`, preserving its mtime so git's racy-clean check still re-reads same-second edits) to a temp `GIT_INDEX_FILE`, `git add -A` (respects .gitignore) → `write-tree` → `commit-tree -p HEAD` → `update-ref refs/gitpanel/backup/<id>`. The real index tree is stored in the backup's commit message trailer (`Index-Tree: <sha>`).
- **Undo** = refuse if any ref in `refs_after` (or HEAD) moved since; checkout the previous branch if the op switched branches; the current branch moves with `git reset --<reset_mode>`, other refs with `update-ref` (`-d` if they didn't exist); remote-tracking refs are never restored; if `backup`, the worktree and index for `paths` are made to match the snapshot (files absent from it are deleted). **Redo** is the mirror image using `backup_after`.
- Refuse (with reason) when: an operation is in progress; a ref to move is checked out in another worktree; the op created commits that are now reachable from the branch's upstream (pushed) and the op itself didn't fetch them — offer revert instead.

## 6. Actions, selection context and dispatch (`actions/`)

```python
class Scope(StrEnum):
    GLOBAL = "global"
    COMMIT = "commit"
    BRANCH = "branch"
    REF = "ref"
    FILE = "file"
    HUNK = "hunk"
    WORKTREE = "worktree"
    STASH = "stash"
    SHELF = "shelf"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SelectionContext:
    session: RepoSession
    screen: str = ""  # "commit", "log", "branches", …
    focus: Scope = Scope.GLOBAL  # what the focused widget is about
    commits: tuple[Commit, ...] = ()
    refs: tuple[Ref, ...] = ()
    files: tuple[FileRef, ...] = ()  # FileRef(path, source: DiffSource, group, status)
    patch: PatchSelection | None = None  # PatchSelection(source, diffs, selection) from patch.py
    worktree: Worktree | None = None
    stash: StashEntry | None = None
    operation: OperationState | None = None
    form: Mapping[str, Any] = {}  # screen inputs, e.g. commit message / amend / author

    def placeholders(self) -> dict[str, str]: ...  # {commit} {branch} {file} {count} {op} …


class Ui(Protocol):
    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool: ...
    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = "", multiline: bool = False
    ) -> str | None: ...
    async def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str | None: ...
    async def form(
        self,
        title: str,
        fields: Sequence[Field],
        *,
        body: str = "",
        ok_label: str = "OK",
        danger: bool = False,
    ) -> dict[str, Any] | None: ...
    # Field(name, label, kind="text"|"bool"|"choice", default, options, placeholder)
    def notify(
        self, message: str, *, severity: Literal["information", "warning", "error"] = "information"
    ) -> None: ...
    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int: ...
    async def run_in_terminal(
        self, argv: Sequence[str], cwd: Path
    ) -> int: ...  # suspend UI, run in real terminal
    def show_error(self, title: str, details: str) -> None: ...
    def post(self, event: str, **data: Any) -> None: ...  # e.g. "commit.done" → screens react
    def open_screen(self, name: str, **kwargs: Any) -> None: ...


@dataclass
class ActionEnv:
    ctx: SelectionContext
    ui: Ui
    session: RepoSession
    entry: JournalEntry | None = None  # mutating actions may refine entry.op ("Commit: <subject>")


@dataclass(frozen=True)
class Action:
    id: str  # "commit.amend", "log.cherry_pick", "custom.open-pr"
    label: str  # palette / menu text
    run: Callable[[ActionEnv], Awaitable[Any]]
    scopes: frozenset[Scope] = frozenset({Scope.GLOBAL})  # where it applies
    when: Callable[[SelectionContext], bool] = always
    key: str | None = (
        None  # default binding (Textual key syntax); "2,alt+9" = alternatives, first shown
    )
    group: str = ""  # context-menu / palette section
    screens: frozenset[str] | None = None  # restrict to screens; None = anywhere
    mutating: bool = False  # dispatcher wraps in journal.mutation(label)
    destructive: bool = False  # → backup snapshot + confirm
    reset_mode: ResetMode = "keep"  # how undo moves the current branch (commit uses "soft")
    confirm: str | None = None  # confirmation text, may use {placeholders}
    silent_backup: bool = False  # destructive (backup snapshot) but safe enough not to ask
    label_fn: Callable[[SelectionContext], str] | None = None  # dynamic label, via label_for(ctx)
    confirm_title: str | None = None
    paths: Callable[[SelectionContext], list[str] | None] | None = None  # for destructive backups
    show_in_palette: bool = True
    description: str = ""


class ActionError(Exception): ...  # user-facing failure, shown as an error notification


class Registry:
    def register(self, action: Action) -> None: ...  # duplicate id → error
    def get(self, action_id: str) -> Action: ...
    def available(self, ctx: SelectionContext) -> list[Action]: ...
    def for_key(self, ctx: SelectionContext, key: str) -> Action | None: ...
    def rebind(self, overrides: Mapping[str, str | None]) -> None: ...  # [keys] in config


class Dispatcher:
    async def dispatch(self, action: Action, env: ActionEnv) -> None: ...
```

Rules:
- Screens publish a fresh `SelectionContext` (immutable) whenever focus/selection changes; the app holds the latest one.
- Key handling: `GitPanelApp.on_key` (keys bubble there before widget bindings run) intercepts keys on the base screen, or on any pushed screen that exposes `context(base)` (tool popups such as Branches); plain dialogs never receive registry keys, asks `registry.for_key(ctx, key)`, dispatches. Navigation keys (arrows, tab, enter in lists, text input) stay with widgets. The footer `KeyHints` widget lists `registry.available(ctx)` that have keys — no static Textual `BINDINGS` for git operations.
- The context menu (`alt+enter`, "Actions…") and the command palette are `registry.available(ctx)`; popups show only screen-restricted actions in their hint bar.
- Dispatcher order: `applies(ctx)` (screens + scopes + `when`) → `confirm` (if set, or `destructive` without `silent_backup`) → `journal.mutation(label, destructive=…, paths=…, reset_mode=…)` if `mutating` → `run` → errors: `GitError` → `ui.notify(severity="error")` + `ui.show_error(details)`; `UndoRefused` → warning; `ActionError` → error notification.
- Outside actions, a `GitError` escaping a view's `@work` worker is reported by the app (`report_git_error`: an error dialog, not stacked twice; a toast while the repository is known to be unreadable) and the app keeps running; any other unhandled error still exits. Event handlers that `await` git must catch `GitError` themselves — Textual stops a widget's message loop after a handler fails — or start a worker instead.
- `Registry.for_key()` prefers screen-restricted actions over unrestricted ones, then non-global scopes over global, so one key (e.g. `space` = stage/unstage) can mean different things in different contexts via `when`.
- Actions are plain async functions over `ActionEnv`; they are tested with a fake `Ui` and a real temp repo, no Textual.

Custom commands (M10) compile `[[commands]]` entries into `Action(id="custom.<id>", mutating=True unless output=="none" and declared read_only)`, with `{commit} {commits} {branch} {ref} {file} {files} {worktree} {repo}` + prompt placeholders substituted shell-quoted.

## 7. Patches (`git/patch.py`)

```python
# DiffSource variants
@dataclass(frozen=True) class WorkingTree: ...         # index → working tree (unstaged)
@dataclass(frozen=True) class Staged: ...              # HEAD → index
@dataclass(frozen=True) class CommitDiff: sha: str; parent: int = 1
@dataclass(frozen=True) class RangeDiff: a: str; b: str
DiffSource = WorkingTree | Staged | CommitDiff | RangeDiff

@dataclass
class DiffLine:
    kind: Literal[" ", "+", "-", "\\"]
    text: str
    old_no: int | None
    new_no: int | None

@dataclass
class Hunk:
    old_start: int; old_len: int; new_start: int; new_len: int
    section: str                                       # text after @@ … @@
    lines: list[DiffLine]

@dataclass
class FileDiff:
    old_path: str | None; new_path: str | None         # None = /dev/null
    status: Literal["A", "D", "M", "R", "C", "T"]
    old_mode: str | None; new_mode: str | None
    binary: bool
    header: list[str]                                  # raw extended header lines
    hunks: list[Hunk]

@dataclass
class Selection:
    whole_files: set[str]
    lines: dict[str, set[tuple[int, int]]]             # path → {(hunk_idx, line_idx)} of +/- lines
    @classmethod
    def hunks(cls, path: str, diff: FileDiff, idxs: Iterable[int]) -> Selection: ...

async def load_diff(git: Git, source: DiffSource, paths: Sequence[str] = (),
                    *, context: int = 3) -> list[FileDiff]: ...
def parse_diff(raw: bytes) -> list[FileDiff]: ...
def build_patch(diffs: Sequence[FileDiff], sel: Selection, *, reverse: bool = False) -> bytes: ...
async def apply(git: Git, patch: bytes, *, to: Literal["index", "worktree", "both"],
                reverse: bool = False, three_way: bool = False) -> None: ...
```

Partial-patch rule (same as `git add -p`): building forward, an unselected `-` line becomes context and an unselected `+` line is dropped; building for reverse application, an unselected `+` line becomes context and an unselected `-` line is dropped. Hunk headers are recomputed. Binary files, mode changes, renames and copies are **whole-file only**.

Usage map:

| Feature | Source | Apply |
|---|---|---|
| Stage selection | `WorkingTree` | `to="index"` |
| Unstage selection | `Staged` | `to="index", reverse=True` |
| Rollback selection | `WorkingTree` | `to="worktree", reverse=True` (destructive) |
| Get/apply from revision | `CommitDiff` | `to="worktree", three_way=True` |
| Shelve selection | `WorkingTree` | save patch, then rollback |
| Patch builder: remove from commit | `CommitDiff` | inside `scripted_rebase` stop: `to="both", reverse=True` + amend |

## 8. Operation state (`git/operations.py`)

```python
class OpKind(StrEnum):
    REBASE = "rebase"
    MERGE = "merge"
    CHERRY_PICK = "cherry-pick"
    REVERT = "revert"
    BISECT = "bisect"
    AM = "am"


@dataclass(frozen=True)
class OperationState:
    kind: OpKind
    step: int | None  # rebase msgnum / sequencer position
    total: int | None
    current: str | None  # sha being applied / bisect candidate
    onto: str | None
    conflicts: int  # unmerged paths count
    head_name: str | None  # branch being rebased
    remaining: int | None  # commits left in a cherry-pick/revert sequence


async def detect(git: Git) -> OperationState | None: ...
```

Detection reads the worktree's git dir: `rebase-merge/` (`msgnum`, `end`, `onto`, `stopped-sha`) or `rebase-apply/`, `MERGE_HEAD`, `CHERRY_PICK_HEAD` / `REVERT_HEAD` with `sequencer/todo`, `BISECT_LOG`. git doesn't record a cherry-pick/revert sequence's length, so gitpanel writes `<git-dir>/gitpanel/sequence-total` when it starts one; `step/total` are derived from that and `remaining`. One `OperationBanner` widget renders any state; continue/skip/abort are ordinary actions with `when=lambda c: c.operation and c.operation.kind in {...}`.

## 9. Scripted rebase (`git/scripted_rebase.py`)

```python
@dataclass
class TodoItem:
    action: Literal[
        "pick",
        "reword",
        "edit",
        "squash",
        "fixup",
        "drop",
        "exec",
        "break",
        "label",
        "reset",
        "merge",
        "update-ref",
    ]
    sha: str | None
    subject: str
    arg: str | None = None


@dataclass(frozen=True)
class StopInfo:
    sha: str  # commit we stopped at (already applied, HEAD)
    item: TodoItem


@dataclass(frozen=True)
class RebaseResult:
    status: Literal["done", "conflict", "aborted"]
    state: OperationState | None


async def todo_for(git: Git, base: str) -> list[TodoItem]: ...
async def run(
    session: RepoSession,
    base: str,
    todo: Sequence[TodoItem],
    *,
    messages: Mapping[str, str] = {},  # sha → new message (reword/squash)
    on_stop: Callable[[StopInfo], Awaitable[None]] | None = None,
    autostash: bool = True,
    rebase_merges: bool | None = None,
) -> RebaseResult: ...
async def resume(session: RepoSession) -> RebaseResult: ...  # after conflicts resolved
```

Mechanism: write the todo + messages to `<git-dir>/gitpanel/rebase/`; run `git rebase -i [--rebase-merges] [--autostash] <base>` with `GIT_SEQUENCE_EDITOR`/`GIT_EDITOR` = `_editor` helper, which copies our todo over git's and answers message prompts from `messages` (falling back to the existing message). For each `edit` stop, call `on_stop`, then `rebase --continue`. A conflict returns `status="conflict"`; the normal resolver + `resume()` finishes it (the staged instructions survive restarts). Callers run `run()` inside a single `journal.mutation`, so the whole rewrite is one undo step.

## 10. Log rows and markers (`git/markers.py`, `git/log.py`, `git/graph.py`)

```python
@dataclass(frozen=True)
class Marker:
    kind: str  # "head" | "branch" | "remote" | "tag" | "worktree" | "picked"
    # | "bisect-good" | "bisect-bad" | "bisect-skip" | "match" | …
    label: str
    style: str  # Textual CSS class / theme token
    dims_row: bool = False  # e.g. already-picked commits render greyed


class MarkerProvider(Protocol):
    name: str
    invalidated_by: ChangeKind
    slow: bool  # Log shows rows first, then fills slow markers in (picked, sync)

    async def markers(
        self, session: RepoSession, shas: Sequence[str], view: LogView
    ) -> Mapping[str, Sequence[Marker]]: ...


@dataclass(frozen=True)
class GraphRow:
    sha: str
    column: int  # lane of this commit
    glyphs: str  # pre-rendered lane characters, e.g. "│ ● ╮"
    lane_colors: tuple[int, ...]


@dataclass(frozen=True)
class LogRow:
    commit: Commit
    graph: GraphRow
    markers: tuple[Marker, ...]
```

`LogView` holds filters (branches, author, since/until, paths, text/regex, sha) and pages commits in chunks of 500 via `git.stream("log", "-z", "--topo-order", fmt, …)`. Lanes are computed incrementally per page (state carried between pages). Markers are requested only for visible + prefetch rows and cached until a provider's `invalidated_by` event fires. Built-in providers: `RefMarkers`, `WorktreeMarkers`, `PickedMarkers` (`rev-list --cherry-mark` over only the commits where HEAD and each branch tip diverge, plus `(cherry picked from commit …)` trailers; cached per (HEAD, tip)); bisect (M10) and search hits later.

## 11. Testing

- `tests/conftest.py` exposes `repo_factory` → `RepoBuilder` with `.commit(files: dict[str, str|None], msg)`, `.branch(name, at=None)`, `.checkout(name)`, `.merge(name)`, `.conflict(path)`, `.bare_remote()`, `.worktree(name, branch)`; every repo has fixed author/committer env so shas are deterministic.
- Backend tests: real git in temp dirs, no mocks of git.
- Action tests: `FakeUi` with scripted answers + real repo; assert repo state and journal entries.
- UI tests: Textual `App.run_test()` pilot driving keys; assert both widgets and repo state. Keep these few and behavioural; logic belongs in backend/action tests.
- Every mutating action has an undo test: run → `journal.undo()` → repo state equals before.
