from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import FileRef, PatchSelection, Scope, SelectionContext
from gitpanel.actions.registry import Action, Dispatcher, Registry
from gitpanel.git import patch as P
from gitpanel.git.operations import detect
from gitpanel.git.session import RepoSession
from gitpanel.git.status import status


@dataclass
class FakeUi:
    confirm_answer: bool = True
    notifications: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)
    confirms: list[str] = field(default_factory=list)

    async def confirm(self, title: str, body: str = "", *, danger: bool = False) -> bool:
        self.confirms.append(body)
        return self.confirm_answer

    prompt_answers: list[str | None] = field(default_factory=list)
    choose_answers: list[str | None] = field(default_factory=list)
    clipboard: list[str] = field(default_factory=list)

    async def prompt(
        self, title: str, *, default: str = "", placeholder: str = "", multiline: bool = False
    ) -> str | None:
        return self.prompt_answers.pop(0) if self.prompt_answers else default

    async def choose(self, title: str, options: Sequence[tuple[str, str]]) -> str | None:
        if self.choose_answers:
            return self.choose_answers.pop(0)
        return options[0][0] if options else None

    def notify(self, message: str, *, severity: str = "information") -> None:
        self.notifications.append((severity, message))

    def show_error(self, title: str, details: str) -> None:
        self.errors.append((title, details))

    async def show_output(self, title: str, stream: AsyncIterator[str]) -> int:
        async for _ in stream:
            pass
        return 0

    async def run_in_terminal(self, argv: Sequence[str], cwd: Path) -> int:
        return 0

    def open_screen(self, name: str, **kwargs: Any) -> None:
        pass

    def post(self, event: str, **data: Any) -> None:
        self.posted.append(event)
        if event == "clipboard":
            self.clipboard.append(data["text"])


@pytest.fixture
async def env(repo):
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    registry = register_builtin(Registry())
    return session, ui, registry, Dispatcher(registry, ui)


async def file_refs(session: RepoSession, group: str, *paths: str) -> tuple[FileRef, ...]:
    st = await status(session.git)
    source = P.Staged() if group == "staged" else P.WorkingTree()
    return tuple(FileRef(p, source, group, st.get(p)) for p in paths)  # type: ignore[arg-type]


async def test_registry_duplicate_and_rebind(env):
    session, _ui, registry, _ = env
    with pytest.raises(ValueError):
        registry.register(Action("app.quit", "x", lambda e: None))  # type: ignore[arg-type,return-value]
    ctx = SelectionContext(session, screen="commit")
    assert registry.for_key(ctx, "q").id == "app.quit"
    registry.rebind({"app.quit": "x", "commit.stage_all": None})
    assert registry.for_key(ctx, "q") is None
    assert registry.for_key(ctx, "x").id == "app.quit"
    assert registry.for_key(ctx, "a") is None
    with pytest.raises(KeyError):
        registry.rebind({"nope": "y"})


async def test_available_respects_scope_and_when(repo, env):
    session, _ui, registry, _ = env
    repo.write("README.md", "changed\n")
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=await file_refs(session, "unstaged", "README.md"),
    )
    ids = {a.id for a in registry.available(ctx)}
    assert "commit.stage" in ids and "commit.unstage" not in ids
    assert registry.for_key(ctx, "space").id == "commit.stage"
    # not on another screen
    other = SelectionContext(session, screen="log", focus=Scope.FILE, files=ctx.files)
    assert "commit.stage" not in {a.id for a in registry.available(other)}


async def test_stage_unstage_files_and_commit(repo, env):
    session, ui, registry, dispatcher = env
    repo.write("README.md", "changed\n")
    repo.write("new.txt", "n\n")
    files = await file_refs(session, "unstaged", "README.md") + await file_refs(
        session, "untracked", "new.txt"
    )
    files = (files[0], FileRef("new.txt", P.WorkingTree(), "untracked", files[1].status))
    ctx = SelectionContext(session, screen="commit", focus=Scope.FILE, files=files)
    await dispatcher.dispatch_id("commit.stage", ctx)
    assert set(repo.git("diff", "--cached", "--name-only").split()) == {"README.md", "new.txt"}

    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=await file_refs(session, "staged", "new.txt"),
    )
    await dispatcher.dispatch_id("commit.unstage", ctx)
    assert repo.git("diff", "--cached", "--name-only").split() == ["README.md"]

    head = repo.head()
    ctx = SelectionContext(session, screen="commit", form={"message": "Update readme\n\nbody"})
    await dispatcher.dispatch_id("commit.commit", ctx)
    assert repo.head() != head
    assert repo.git("log", "-1", "--format=%s").strip() == "Update readme"
    assert ui.posted == ["commit.done"]
    [entry] = session.journal.entries()
    assert entry.op == "Commit: Update readme" and entry.reset_mode == "soft"


async def test_commit_without_message_or_changes(repo, env):
    session, ui, _r, dispatcher = env
    await dispatcher.dispatch_id("commit.commit", SelectionContext(session, screen="commit"))
    assert ui.notifications[-1] == ("error", "Enter a commit message")
    ctx = SelectionContext(session, screen="commit", form={"message": "x"})
    await dispatcher.dispatch_id("commit.commit", ctx)
    assert ui.notifications[-1] == ("error", "Nothing staged to commit")


async def test_amend(repo, env):
    session, _ui, _r, dispatcher = env
    parent = repo.git("rev-parse", "HEAD~0").strip()
    repo.commit({"f.txt": "1\n"}, "orig")
    orig = repo.head()
    ctx = SelectionContext(session, screen="commit", form={"message": "reworded", "amend": True})
    await dispatcher.dispatch_id("commit.commit", ctx)
    assert repo.head() != orig
    assert repo.git("rev-parse", "HEAD~1").strip() == parent
    assert repo.git("log", "-1", "--format=%s").strip() == "reworded"


async def test_stage_hunk_via_patch_selection(repo, env):
    session, _ui, _r, dispatcher = env
    base = "".join(f"{i}\n" for i in range(40))
    repo.commit({"h.txt": base})
    repo.write("h.txt", base.replace("3\n", "three\n", 1).replace("30\n", "thirty\n"))
    diffs = tuple(await P.load_diff(session.git, P.WorkingTree()))
    sel = P.Selection.hunks(diffs[0], [1])
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.HUNK,
        patch=PatchSelection(P.WorkingTree(), diffs, sel),
    )
    await dispatcher.dispatch_id("commit.stage", ctx)
    staged = repo.staged_diff()
    assert "+thirty" in staged and "three" not in staged


async def test_rollback_requires_confirm_and_is_undoable(repo, env):
    session, ui, _r, dispatcher = env
    repo.write("README.md", "dirty\n")
    files = await file_refs(session, "unstaged", "README.md")
    ctx = SelectionContext(session, screen="commit", focus=Scope.FILE, files=files)

    ui.confirm_answer = False
    await dispatcher.dispatch_id("commit.rollback", ctx)
    assert repo.read("README.md") == "dirty\n"
    assert session.journal.entries() == []

    ui.confirm_answer = True
    await dispatcher.dispatch_id("commit.rollback", ctx)
    assert repo.read("README.md") == "hello\n"
    [entry] = session.journal.entries()
    assert entry.backup and entry.paths == ["README.md"]

    await dispatcher.dispatch_id("app.undo", SelectionContext(session, screen="commit"))
    assert repo.read("README.md") == "dirty\n", ui.notifications


async def test_rollback_untracked_deletes_and_undo_restores(repo, env):
    session, _ui, _r, dispatcher = env
    repo.write("junk.txt", "junk\n")
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(FileRef("junk.txt", P.WorkingTree(), "untracked"),),
    )
    await dispatcher.dispatch_id("commit.rollback", ctx)
    assert not (repo.path / "junk.txt").exists()
    await dispatcher.dispatch_id("app.undo", SelectionContext(session))
    assert repo.read("junk.txt") == "junk\n"


async def test_git_error_is_reported(repo, env):
    session, ui, registry, dispatcher = env

    async def boom(e):
        await e.session.git.run("checkout", "no-such-branch")

    registry.register(Action("test.boom", "Boom", boom))
    await dispatcher.dispatch_id("test.boom", SelectionContext(session))
    assert ui.notifications[-1][0] == "error"
    assert "no-such-branch" in ui.errors[-1][1]


async def test_abort_merge(repo, env):
    session, ui, _r, dispatcher = env
    repo.conflict()
    op = await detect(session.git)
    ctx = SelectionContext(session, operation=op)
    assert not _r.get("op.continue").applies(ctx)  # conflicts remain
    await dispatcher.dispatch_id("op.abort", ctx)
    assert "merge" in ui.confirms[-1]
    assert await detect(session.git) is None


async def test_ignore_untracked(repo, env):
    session, _ui, _r, dispatcher = env
    repo.write("build/out.o", "x")
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(FileRef("build/out.o", P.WorkingTree(), "untracked"),),
    )
    await dispatcher.dispatch_id("commit.ignore", ctx)
    assert "/build/out.o" in repo.read(".gitignore")
