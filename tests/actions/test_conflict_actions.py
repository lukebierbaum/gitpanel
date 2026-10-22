from __future__ import annotations

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import FileRef, Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import conflicts as CF
from gitpanel.git import patch as P
from gitpanel.git.session import RepoSession

from ..git.test_conflicts import make_conflict
from .test_actions import FakeUi


async def test_resolver_actions_edit_both_and_apply(repo):
    make_conflict(repo)
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    d = Dispatcher(register_builtin(Registry()), ui)
    model = await CF.merge_model(session.git, "f.txt")

    def ctx(i):
        return SelectionContext(
            session, screen="resolver", focus=Scope.CONFLICT, form={"merge": model, "chunk": i}
        )

    await d.dispatch_id("resolver.take_both_reversed", ctx(0))
    ui.prompt_answers = ["custom five\n"]
    await d.dispatch_id("resolver.edit", ctx(1))
    assert model.unresolved == 0
    await d.dispatch_id("resolver.apply", ctx(1))
    assert "one-other\nONE-main\n" in repo.read("f.txt")
    assert "custom five\n" in repo.read("f.txt")
    assert "resolver.done" in ui.posted
    assert await CF.conflicts(session.git) == []


async def test_apply_with_unresolved_asks(repo):
    make_conflict(repo)
    session = await RepoSession.open(repo.path)
    ui = FakeUi(confirm_answer=False)
    d = Dispatcher(register_builtin(Registry()), ui)
    model = await CF.merge_model(session.git, "f.txt")
    await d.dispatch_id(
        "resolver.apply",
        SelectionContext(session, screen="resolver", form={"merge": model, "chunk": 0}),
    )
    assert await CF.conflicts(session.git) != []


async def test_accept_theirs_file_action_is_undoable(repo):
    make_conflict(repo)
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    d = Dispatcher(register_builtin(Registry()), ui)
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(FileRef("f.txt", P.WorkingTree(), "conflict"),),
    )
    await d.dispatch_id("conflict.accept_theirs", ctx)
    assert "one-other" in repo.read("f.txt") and "<<<" not in repo.read("f.txt")
    await d.dispatch_id("conflict.resolve", ctx)  # no longer a conflict → opens resolver anyway
    assert ui.opened[-1] == ("resolver", {"path": "f.txt"})
