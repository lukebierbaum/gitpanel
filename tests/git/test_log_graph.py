from __future__ import annotations

from gitpanel.git.graph import GraphBuilder, rows_for
from gitpanel.git.log import LogFilter, commit_details, containing_branches, load_page
from gitpanel.git.markers import collect, default_providers
from gitpanel.git.models import Commit
from gitpanel.git.session import RepoSession


def C(sha: str, *parents: str) -> Commit:
    return Commit(sha=sha, parents=parents)


def test_linear_graph_single_lane():
    rows = rows_for([C("c", "b"), C("b", "a"), C("a")])
    assert [r.column for r in rows] == [0, 0, 0]
    assert [r.text for r in rows] == ["●", "●", "◉"]


def test_branch_and_merge_glyphs():
    # m merges f (feature) into main; history: m(main2,f) ; f(b) ; main2(b) ; b(a) ; a
    rows = rows_for([C("m", "main2", "f"), C("f", "b"), C("main2", "b"), C("b", "a"), C("a")])
    m, f, main2, b, a = rows
    assert m.column == 0 and m.text.startswith("◆") and "╮" in m.text
    assert f.column == 1 and f.text == "│ ●"
    assert main2.column == 0 and main2.text.startswith("●")
    assert b.column == 0 and "╯" in b.text  # feature lane joins back
    assert a.text.strip() == "◉"


def test_octopus_merge():
    rows = rows_for([C("o", "a", "b", "c"), C("c", "r"), C("b", "r"), C("a", "r"), C("r")])
    assert rows[0].text == "◆─┬─╮"
    assert rows[-1].column == 0


def test_lanes_survive_page_boundary():
    commits = [C("m", "x", "f"), C("f", "b"), C("x", "b"), C("b")]
    whole = rows_for(commits)
    g = GraphBuilder()
    paged = g.add_all(commits[:2]) + g.add_all(commits[2:])
    assert [r.cells for r in whole] == [r.cells for r in paged]


async def test_load_page_and_filters(repo):
    repo.commit({"a.txt": "1\n"}, "feat: add a %s weird \x1f? no")
    repo.git(
        "-c",
        "user.name=Other",
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "chore: other author",
        "--author=Other Person <other@example.com>",
    )
    repo.commit({"dir/b.txt": "2\n"}, "fix: b ✓")
    g = repo.g
    all_ = await load_page(g, LogFilter())
    assert [c.subject for c in all_][:2] == ["fix: b ✓", "chore: other author"]
    assert len(all_) == 4
    assert [c.subject for c in await load_page(g, LogFilter(author="Other"))] == [
        "chore: other author"
    ]
    assert [c.subject for c in await load_page(g, LogFilter(text="^fix"))] == ["fix: b ✓"]
    assert [c.subject for c in await load_page(g, LogFilter(paths=("dir",)))] == ["fix: b ✓"]
    page2 = await load_page(g, LogFilter(), skip=2, limit=1)
    assert len(page2) == 1 and page2[0].sha == all_[2].sha
    assert all_[0].author_date is not None


async def test_branch_filter_and_details(repo):
    repo.branch("side")
    repo.checkout("side")
    sha = repo.commit({"s.txt": "s\n"}, "side work\n\nwith a body")
    repo.checkout("main")
    repo.commit({"m.txt": "m\n"}, "main work")
    only_main = await load_page(repo.g, LogFilter(refs=("main",)))
    assert "side work" not in [c.subject for c in only_main]
    everything = await load_page(repo.g, LogFilter())
    assert "side work" in [c.subject for c in everything]
    d = await commit_details(repo.g, sha)
    assert d.subject == "side work" and d.body == "side work\n\nwith a body"
    assert await containing_branches(repo.g, sha) == ["side"]


async def test_markers(repo):
    repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    first = repo.head()
    repo.git("tag", "v1")
    repo.commit({"n.txt": "n\n"}, "next")
    repo.worktree("wt", "wtbranch")
    session = await RepoSession.open(repo.path)
    marks = await collect(default_providers(), session, [repo.head(), first])
    head_labels = [(m.kind, m.label) for m in marks[repo.head()]]
    assert ("head", "main") in head_labels
    assert ("branch", "wtbranch") in head_labels
    assert any(k == "worktree" for k, _ in head_labels)
    first_labels = [(m.kind, m.label) for m in marks[first]]
    assert ("remote", "origin/main") in first_labels and ("tag", "v1") in first_labels


async def test_sync_markers_local_and_remote_only(repo, tmp_path):
    import subprocess

    from gitpanel.git.markers import SyncMarkers

    session = await RepoSession.open(repo.path)
    no_remote = await SyncMarkers().markers(session, [repo.head()])
    assert no_remote == {}  # no remotes → no labels
    origin = repo.bare_remote()
    repo.git("push", "-q", "-u", "origin", "main")
    pushed = repo.head()
    unpushed = repo.commit({"l.txt": "l\n"}, "local only")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    subprocess.run(
        ["git", "-C", str(other), "commit", "-q", "--allow-empty", "-m", "theirs"], check=True
    )
    subprocess.run(["git", "-C", str(other), "push", "-q"], check=True)
    repo.git("fetch", "-q")
    incoming = repo.git("rev-parse", "origin/main").strip()
    marks = await SyncMarkers().markers(session, [pushed, unpushed, incoming])
    assert pushed not in marks
    assert marks[unpushed][0].kind == "unpushed"
    assert marks[incoming][0].kind == "incoming"
