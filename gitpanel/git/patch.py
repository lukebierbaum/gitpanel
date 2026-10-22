"""Source-agnostic diffs, hunk/line selection and partial patch application.

See docs/architecture.md §7. One model serves staging, unstaging, rollback,
shelving, "apply from revision" and the patch builder.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .runner import Git

# --- diff sources -----------------------------------------------------------


@dataclass(frozen=True)
class WorkingTree:
    """index → working tree (unstaged changes)."""


@dataclass(frozen=True)
class Staged:
    """HEAD → index (staged changes)."""


@dataclass(frozen=True)
class CommitDiff:
    """parent → commit. `parent` is 1-based, as in `sha^N`."""

    sha: str
    parent: int = 1


@dataclass(frozen=True)
class RangeDiff:
    a: str
    b: str


DiffSource = WorkingTree | Staged | CommitDiff | RangeDiff

# --- model ------------------------------------------------------------------

LineKind = Literal[" ", "+", "-", "\\"]


@dataclass
class DiffLine:
    kind: LineKind
    text: str  # without the leading marker, without trailing "\n"
    old_no: int | None
    new_no: int | None

    @property
    def is_change(self) -> bool:
        return self.kind in "+-"


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    section: str
    lines: list[DiffLine]

    @property
    def header(self) -> str:
        old = _range(self.old_start, self.old_len)
        new = _range(self.new_start, self.new_len)
        return f"@@ -{old} +{new} @@{self.section}"

    def change_indexes(self) -> list[int]:
        return [i for i, ln in enumerate(self.lines) if ln.is_change]


@dataclass
class FileDiff:
    old_path: str | None  # None = /dev/null
    new_path: str | None
    status: Literal["A", "D", "M", "R", "C", "T"]
    old_mode: str | None
    new_mode: str | None
    binary: bool
    header: list[str]  # "diff --git" line + extended headers (+ ---/+++ when present)
    hunks: list[Hunk] = field(default_factory=list)
    binary_patch: list[str] = field(default_factory=list)  # "GIT binary patch" block

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def partial_allowed(self) -> bool:
        """Line-level selection is allowed only for plain text content changes."""
        return (
            not self.binary
            and bool(self.hunks)
            and self.status in ("A", "D", "M")
            and (self.old_mode == self.new_mode or self.status in ("A", "D"))
        )

    @property
    def additions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "+")

    @property
    def deletions(self) -> int:
        return sum(1 for h in self.hunks for ln in h.lines if ln.kind == "-")


@dataclass
class Selection:
    """Which parts of a set of FileDiffs are selected.

    `whole_files` holds paths selected entirely. `lines` maps a path to the
    (hunk index, line index) pairs of selected +/- lines.
    """

    whole_files: set[str] = field(default_factory=set)
    lines: dict[str, set[tuple[int, int]]] = field(default_factory=dict)

    @classmethod
    def files(cls, paths: Iterable[str]) -> Selection:
        return cls(whole_files=set(paths))

    @classmethod
    def hunks(cls, diff: FileDiff, idxs: Iterable[int]) -> Selection:
        sel = cls()
        for hi in idxs:
            sel.add_hunk(diff, hi)
        return sel

    def add_hunk(self, diff: FileDiff, hunk_idx: int) -> None:
        bucket = self.lines.setdefault(diff.path, set())
        bucket.update((hunk_idx, li) for li in diff.hunks[hunk_idx].change_indexes())

    def add_lines(self, path: str, pairs: Iterable[tuple[int, int]]) -> None:
        self.lines.setdefault(path, set()).update(pairs)

    def is_empty(self) -> bool:
        return not self.whole_files and not any(self.lines.values())

    def covers_file(self, diff: FileDiff) -> bool:
        if diff.path in self.whole_files:
            return True
        chosen = self.lines.get(diff.path, set())
        all_changes = {(hi, li) for hi, h in enumerate(diff.hunks) for li in h.change_indexes()}
        return bool(all_changes) and all_changes <= chosen

    def paths(self) -> set[str]:
        return self.whole_files | {p for p, s in self.lines.items() if s}


# --- parsing ----------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
}


def _range(start: int, length: int) -> str:
    return f"{start}" if length == 1 else f"{start},{length}"


def unquote_path(s: str) -> str:
    """Undo git's C-style path quoting (`"a/tab\\there"`)."""
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    body = s[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            n = body[i + 1]
            if n in "01234567":
                out.append(int(body[i + 1 : i + 4], 8))
                i += 4
                continue
            out += _ESCAPES.get(n, n).encode()
            i += 2
            continue
        out += c.encode("utf-8", "surrogateescape")
        i += 1
    return out.decode("utf-8", "surrogateescape")


def quote_path(prefix: str, path: str) -> str:
    """`prefix + path`, C-quoted the way git does when the path needs it."""
    full = prefix + path
    if not any(c in full for c in '"\\') and all(c >= " " and c != "\x7f" for c in full):
        return full
    rev = {v: k for k, v in _ESCAPES.items()}
    out = []
    for ch in full:
        if ch in rev:
            out.append("\\" + rev[ch])
        elif ch < " " or ch == "\x7f":
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _strip_prefix(p: str) -> str | None:
    # git appends a TAB to ---/+++ paths that contain spaces
    p = unquote_path(p.rstrip("\t"))
    if p == "/dev/null":
        return None
    if p[:2] in ("a/", "b/"):
        return p[2:]
    return p


def _git_line_paths(line: str) -> tuple[str | None, str | None]:
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        end = _quoted_end(rest)
        a, b = rest[: end + 1], rest[end + 2 :]
        return _strip_prefix(a), _strip_prefix(b)
    if rest.endswith('"'):
        start = rest.rfind(' "')
        return _strip_prefix(rest[:start]), _strip_prefix(rest[start + 1 :])
    # unquoted and same path on both sides: "a/P b/P"
    half = (len(rest) - 1) // 2
    a, b = rest[:half], rest[half + 1 :]
    if a[2:] == b[2:]:
        return a[2:], b[2:]
    a, _, b = rest.partition(" b/")
    return _strip_prefix(a), b


def _quoted_end(s: str) -> int:
    i = 1
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return i
        i += 1
    return len(s) - 1


def parse_diff(raw: bytes | str) -> list[FileDiff]:
    text = raw.decode("utf-8", "surrogateescape") if isinstance(raw, bytes) else raw
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0
    in_binary = False

    for line in lines:
        if (
            line.startswith("diff --git ")
            or line.startswith("diff --cc ")
            or line.startswith("diff --combined ")
        ):
            old, new = _git_line_paths(line) if line.startswith("diff --git ") else (None, None)
            cur = FileDiff(old, new, "M", None, None, False, [line])
            files.append(cur)
            hunk = None
            in_binary = False
            continue
        if cur is None:
            continue
        if in_binary:
            cur.binary_patch.append(line)
            continue
        if hunk is not None and hunk_complete(hunk, old_no, new_no) and not line.startswith("\\"):
            hunk = None
        if hunk is not None:
            kind = line[0] if line else " "
            body = line[1:]
            if kind == " ":
                hunk.lines.append(DiffLine(" ", body, old_no, new_no))
                old_no += 1
                new_no += 1
            elif kind == "-":
                hunk.lines.append(DiffLine("-", body, old_no, None))
                old_no += 1
            elif kind == "+":
                hunk.lines.append(DiffLine("+", body, None, new_no))
                new_no += 1
            else:
                hunk.lines.append(DiffLine("\\", body, None, None))
            continue
        m = _HUNK_RE.match(line)
        if m:
            o_start, o_len, n_start, n_len, section = m.groups()
            hunk = Hunk(
                int(o_start),
                1 if o_len is None else int(o_len),
                int(n_start),
                1 if n_len is None else int(n_len),
                section,
                [],
            )
            cur.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            if hunk.old_len == 0:
                old_no += 1
            if hunk.new_len == 0:
                new_no += 1
            continue
        # extended header lines
        cur.header.append(line)
        if line.startswith("new file mode "):
            cur.status, cur.new_mode, cur.old_path = "A", line.split()[-1], None
        elif line.startswith("deleted file mode "):
            cur.status, cur.old_mode, cur.new_path = "D", line.split()[-1], None
        elif line.startswith("old mode "):
            cur.old_mode = line.split()[-1]
        elif line.startswith("new mode "):
            cur.new_mode = line.split()[-1]
        elif line.startswith("rename from "):
            cur.status, cur.old_path = "R", unquote_path(line[len("rename from ") :])
        elif line.startswith("rename to "):
            cur.new_path = unquote_path(line[len("rename to ") :])
        elif line.startswith("copy from "):
            cur.status, cur.old_path = "C", unquote_path(line[len("copy from ") :])
        elif line.startswith("copy to "):
            cur.new_path = unquote_path(line[len("copy to ") :])
        elif line.startswith("index "):
            parts = line.split()
            if len(parts) == 3:
                cur.old_mode = cur.old_mode or parts[2]
                cur.new_mode = cur.new_mode or parts[2]
        elif line.startswith("--- "):
            p = _strip_prefix(line[4:])
            if p is not None or cur.status == "A":
                cur.old_path = p
        elif line.startswith("+++ "):
            p = _strip_prefix(line[4:])
            if p is not None or cur.status == "D":
                cur.new_path = p
        elif line.startswith("Binary files "):
            cur.binary = True
        elif line == "GIT binary patch":
            cur.binary = True
            in_binary = True
            cur.header.pop()
            cur.binary_patch.append(line)
    return files


def hunk_complete(hunk: Hunk, old_no: int, new_no: int) -> bool:
    """True when `hunk` already holds all lines its header promised."""
    old_end = hunk.old_start + hunk.old_len + (1 if hunk.old_len == 0 else 0)
    new_end = hunk.new_start + hunk.new_len + (1 if hunk.new_len == 0 else 0)
    return old_no >= old_end and new_no >= new_end


# --- building partial patches -----------------------------------------------


def _emit_file_whole(fd: FileDiff) -> list[str]:
    out = list(fd.header)
    for h in fd.hunks:
        out.append(h.header)
        out.extend(ln.kind + ln.text for ln in h.lines)
    out.extend(fd.binary_patch)
    return out


def _modification_header(fd: FileDiff) -> list[str]:
    """Header for a partial patch of an added/deleted file, rewritten as a modification."""
    a, b = quote_path("a/", fd.path), quote_path("b/", fd.path)
    return [f"diff --git {a} {b}", f"--- {a}", f"+++ {b}"]


def _plain_header(fd: FileDiff, reverse: bool) -> list[str]:
    """Extended header for a partial patch.

    Forward patches keep the `index` line so `--3way` can find the pre-image;
    for reverse application its hashes would be wrong, so it is dropped.
    """
    if not reverse:
        return list(fd.header)
    return [h for h in fd.header if not h.startswith("index ")]


def build_patch(diffs: Sequence[FileDiff], sel: Selection, *, reverse: bool = False) -> bytes:
    """Build a patch containing the selected parts of `diffs`.

    Forward (`reverse=False`, to be applied as-is): unselected `-` lines become
    context and unselected `+` lines are dropped. For reverse application
    (`git apply -R`): unselected `+` lines become context and unselected `-`
    lines are dropped. Hunk headers are recomputed.
    """
    out: list[str] = []
    for fd in diffs:
        path = fd.path
        if path in sel.whole_files or sel.covers_file(fd):
            out.extend(_emit_file_whole(fd))
            continue
        chosen = sel.lines.get(path)
        if not chosen:
            continue
        if not fd.partial_allowed:
            raise ValueError(f"{path}: only whole-file selection is possible")
        hunks = _partial_hunks(fd, chosen, reverse)
        if not hunks:
            continue
        # A partial new file applied in reverse, or a partial deletion applied
        # forward, leaves the file in place: express it as a modification.
        if (fd.status == "A" and reverse) or (fd.status == "D" and not reverse):
            header = _modification_header(fd)
        else:
            header = _plain_header(fd, reverse)
        out.extend(header)
        for h in hunks:
            out.append(h.header)
            out.extend(ln.kind + ln.text for ln in h.lines)
    if not out:
        return b""
    return ("\n".join(out) + "\n").encode("utf-8", "surrogateescape")


def _partial_hunks(fd: FileDiff, chosen: set[tuple[int, int]], reverse: bool) -> list[Hunk]:
    result: list[Hunk] = []
    delta = 0  # cumulative (new_len - old_len) of emitted hunks on the side we recompute
    for hi, h in enumerate(fd.hunks):
        if not any((hi, li) in chosen for li in h.change_indexes()):
            # In a forward patch an unselected hunk contributes nothing; same for reverse.
            continue
        lines: list[DiffLine] = []
        dropped_prev = False
        for li, ln in enumerate(h.lines):
            if ln.kind == "\\":
                if not dropped_prev:
                    lines.append(ln)
                continue
            selected = (hi, li) in chosen
            if ln.kind == " " or selected:
                lines.append(ln)
                dropped_prev = False
            elif ln.kind == "-":
                if reverse:
                    dropped_prev = True
                else:
                    lines.append(DiffLine(" ", ln.text, ln.old_no, ln.old_no))
                    dropped_prev = False
            else:  # unselected "+"
                if reverse:
                    lines.append(DiffLine(" ", ln.text, ln.new_no, ln.new_no))
                    dropped_prev = False
                else:
                    dropped_prev = True
        old_len = sum(1 for ln in lines if ln.kind in " -")
        new_len = sum(1 for ln in lines if ln.kind in " +")
        if reverse:
            # post-image (new side) is what exists now; keep it, recompute old side
            new_start = h.new_start
            old_start = new_start - delta
            if new_len == 0 and old_len > 0:
                old_start += 1
            elif old_len == 0 and new_len > 0:
                old_start -= 1
            delta += new_len - old_len
        else:
            old_start = h.old_start
            new_start = old_start + delta
            if old_len == 0 and new_len > 0:
                new_start += 1
            elif new_len == 0 and old_len > 0:
                new_start -= 1
            delta += new_len - old_len
        result.append(
            Hunk(max(old_start, 0), old_len, max(new_start, 0), new_len, h.section, lines)
        )
    return result


# --- loading and applying -----------------------------------------------------

DIFF_OPTS = ("--no-color", "--no-ext-diff", "--binary", "--full-index", "-M")


async def _parents(git: Git, sha: str) -> list[str]:
    res = await git.run("rev-list", "--parents", "-n", "1", sha, read_only=True)
    return res.text.split()[1:]


async def empty_tree(git: Git) -> str:
    res = await git.run("hash-object", "-t", "tree", "--stdin", input=b"", read_only=True)
    return res.text.strip()


async def load_diff(
    git: Git,
    source: DiffSource,
    paths: Sequence[str] = (),
    *,
    context: int = 3,
    include_untracked: Sequence[str] = (),
) -> list[FileDiff]:
    """Load and parse the diff for `source`, limited to `paths` if given.

    `include_untracked` appends new-file diffs for those untracked paths
    (WorkingTree only), so they can be shown and staged like any other file.
    """
    opts = (*DIFF_OPTS, f"-U{context}")
    spec = ("--", *paths) if paths else ()
    match source:
        case WorkingTree():
            args = ("diff", *opts, *spec)
        case Staged():
            args = ("diff", "--cached", *opts, *spec)
        case CommitDiff(sha=sha, parent=parent):
            parents = await _parents(git, sha)
            if not parents:
                base = await empty_tree(git)
            else:
                base = parents[parent - 1]
            args = ("diff", *opts, base, sha, *spec)
        case RangeDiff(a=a, b=b):
            args = ("diff", *opts, a, b, *spec)
        case _:
            raise TypeError(source)
    res = await git.run(*args, read_only=True)
    diffs = parse_diff(res.stdout)
    for p in include_untracked:
        r = await git.run(
            "diff", "--no-index", *opts, "--", "/dev/null", p, check=False, read_only=True
        )
        if r.returncode not in (0, 1):
            continue
        diffs.extend(parse_diff(r.stdout))
    return diffs


async def apply(
    git: Git,
    patch: bytes,
    *,
    to: Literal["index", "worktree", "both"],
    reverse: bool = False,
    three_way: bool = False,
) -> None:
    if not patch:
        return
    args = ["apply", "--whitespace=nowarn", "--recount"]
    if to == "index":
        args.append("--cached")
    elif to == "both":
        args.append("--index")
    if reverse:
        args.append("-R")
    if three_way:
        args.append("--3way")
    args.append("-")
    await git.run(*args, input=patch)
