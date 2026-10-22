from __future__ import annotations

from gitpanel.actions.builtin import register_builtin
from gitpanel.actions.context import FileRef, PatchSelection, Scope, SelectionContext
from gitpanel.actions.custom import compile_command, register_custom, render
from gitpanel.actions.registry import Dispatcher, Registry
from gitpanel.git import patch as P
from gitpanel.git.log import commit_details
from gitpanel.git.operations import detect
from gitpanel.git.session import RepoSession

from .test_actions import FakeUi


async def setup(repo):
    session = await RepoSession.open(repo.path)
    ui = FakeUi()
    registry = register_builtin(Registry())
    return session, ui, registry, Dispatcher(registry, ui)


async def test_patch_move_new_via_action(repo):
    sha = repo.commit({"a.txt": "a\n", "b.txt": "b\n"}, "a and b")
    repo.commit({"c.txt": "c\n"}, "c")
    session, ui, _r, d = await setup(repo)
    diffs = tuple(await P.load_diff(session.git, P.CommitDiff(sha)))
    ctx = SelectionContext(
        session,
        screen="log",
        focus=Scope.HUNK,
        patch=PatchSelection(P.CommitDiff(sha), diffs, P.Selection.files(["b.txt"])),
    )
    ui.prompt_answers = ["just b"]
    await d.dispatch_id("patch.move_new", ctx)
    assert repo.git("log", "--format=%s", "-3").split("\n")[:3] == ["c", "just b", "a and b"]
    await d.dispatch_id("app.undo", SelectionContext(session))
    assert repo.git("log", "--format=%s", "-2").split("\n")[:2] == ["c", "a and b"]


async def test_bisect_actions(repo):
    shas = [repo.commit({"v.txt": f"{i}\n"}, f"v{i}") for i in range(6)]
    session, ui, _r, d = await setup(repo)

    async def ctx(sha):
        op = await detect(session.git)
        return SelectionContext(
            session,
            screen="log",
            focus=Scope.COMMIT,
            commits=(await commit_details(session.git, sha),),
            operation=op,
        )

    await d.dispatch_id("bisect.bad", await ctx(shas[-1]))
    assert ui.notifications[-1][1].endswith("now mark a good commit")
    await d.dispatch_id("bisect.good", await ctx(shas[0]))
    for _ in range(6):
        head = repo.head()
        v = int(repo.read("v.txt"))
        await d.dispatch_id("bisect.good" if v < 3 else "bisect.bad", await ctx(head))
        if "first bad commit" in ui.notifications[-1][1]:
            break
    assert shas[3][:10] in ui.notifications[-1][1]
    await d.dispatch_id("bisect.reset", await ctx(repo.head()))
    assert await detect(session.git) is None


def test_render_quotes_values():
    out = render("echo {branch} {files}", {"branch": "a b; rm -rf /", "files": "x y"})
    assert out == "echo 'a b; rm -rf /' x y"


async def test_custom_command_modes(repo, tmp_path):
    session, ui, registry, d = await setup(repo)
    errors = register_custom(
        registry,
        [
            {"id": "touch", "label": "Touch", "run": "touch {file}.marker", "context": "file"},
            {
                "id": "echo",
                "run": "echo hi {msg}",
                "output": "popup",
                "read_only": True,
                "prompts": [{"name": "msg", "label": "Message"}],
            },
            {"id": "fail", "run": "exit 3"},
            {"id": "bad", "run": "x", "context": "nowhere"},
            {"id": "touch", "run": "dup"},
        ],
    )
    assert len(errors) == 2
    fctx = SelectionContext(
        session,
        screen="commit",
        focus=Scope.FILE,
        files=(FileRef("README.md", P.WorkingTree(), "unstaged"),),
    )
    assert registry.get("custom.touch").applies(fctx)
    assert not registry.get("custom.touch").applies(SelectionContext(session))
    await d.dispatch_id("custom.touch", fctx)
    assert (repo.path / "README.md.marker").exists()
    ui.prompt_answers = ["there"]
    await d.dispatch_id("custom.echo", SelectionContext(session))
    await d.dispatch_id("custom.fail", SelectionContext(session))
    assert ui.notifications[-1][0] == "error" and "exit 3" in ui.errors[-1][1]
    assert compile_command({"id": "t", "run": "x", "output": "terminal"}).mutating
