from __future__ import annotations

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import FileRef, Scope, SelectionContext
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import patch as P
from gitpanel.git import shelf as SH
from gitpanel.git import stash as ST
from gitpanel.git.changelists import Changelists
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


async def setup(repo):
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    return session, ui, Dispatcher(register_builtin(Registry()), ui)


async def test_history_and_annotate_open_screens(repo):
    session, ui, d = await setup(repo)
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(FileRef("README.md", P.WorkingTree(), "unstaged"),),
    )
    await d.dispatch_id("file.history", ctx)
    await d.dispatch_id("file.annotate", ctx)
    assert ui.opened == [
        ("history", {"path": "README.md"}),
        ("annotate", {"path": "README.md", "rev": None}),
    ]
    line = {"sha": repo.head(), "orig_line": 1, "orig_path": "README.md", "uncommitted": False}
    actx = SelectionContext(
        session, screen="annotate", form={"blame_line": line, "path": "README.md"}
    )
    await d.dispatch_id("annotate.previous", actx)
    assert ui.opened[-1] == ("annotate", {"path": "README.md", "rev": f"{repo.head()}^", "line": 1})


async def test_shelve_then_unshelve_and_undo(repo):
    session, ui, d = await setup(repo)
    repo.write("README.md", "changed\n")
    repo.write("new.txt", "n\n")
    ctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(
            FileRef("README.md", P.WorkingTree(), "unstaged"),
            FileRef("new.txt", P.WorkingTree(), "untracked"),
        ),
    )
    ui.prompt_answers = ["my shelf"]
    await d.dispatch_id("shelf.shelve", ctx)
    assert repo.read("README.md") == "hello\n" and not (repo.path / "new.txt").exists()
    [shelf] = SH.list_shelves(session)
    await d.dispatch_id(
        "shelf.unshelve",
        SelectionContext(session, screen="shelf", focus=Scope.SHELF, form={"shelf": shelf}),
    )
    assert repo.read("README.md") == "changed\n" and repo.read("new.txt") == "n\n"


async def test_stash_push_pop(repo):
    session, ui, d = await setup(repo)
    repo.write("README.md", "stash me\n")
    ui.form_answers = [{"message": "wip"}]
    await d.dispatch_id(
        "stash.push",
        SelectionContext(session, screen="commit", form={"all_changed": ("README.md",)}),
    )
    [s] = await ST.stashes(session.git)
    assert repo.read("README.md") == "hello\n"
    await d.dispatch_id(
        "stash.pop", SelectionContext(session, screen="shelf", focus=Scope.STASH, stash=s)
    )
    assert repo.read("README.md") == "stash me\n" and await ST.stashes(session.git) == []


async def test_changelist_move_and_stage(repo):
    session, ui, d = await setup(repo)
    repo.commit({"a.txt": "a\n", "b.txt": "b\n"})
    repo.write("a.txt", "A\n")
    repo.write("b.txt", "B\n")
    files = (FileRef("a.txt", P.WorkingTree(), "unstaged"),)
    ui.choose_answers = ["__new__"]
    ui.prompt_answers = ["Feature"]
    await d.dispatch_id(
        "changelist.move", SelectionContext(session, screen="commit", focus=Scope.FILE, files=files)
    )
    cl = Changelists.load(session.private_dir() / "changelists.json")
    assert cl.list_for("a.txt") == "Feature" and cl.list_for("b.txt") == "Changes"
    await d.dispatch_id(
        "changelist.stage",
        SelectionContext(
            session, screen="commit", focus=Scope.FILE, files=files, form={"changelist": "Feature"}
        ),
    )
    assert repo.git("diff", "--cached", "--name-only").split() == ["a.txt"]
