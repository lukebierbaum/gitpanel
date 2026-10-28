"""A stand-in for the `gh` CLI used by the tests (no network).

State lives in the JSON file named by $FAKE_GH_STATE:
  {"repo": {...}, "bare": "<path of the 'GitHub' bare repo>", "me": "me",
   "prs": [<gh --json style PR dicts>], "threads": {"<n>": [<thread nodes>]}, "calls": [[args]]}
Git operations (checkout, create) act on real repositories.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys

STATE = os.environ["FAKE_GH_STATE"]


def load() -> dict:
    with open(STATE) as f:
        return json.load(f)


def save(state: dict) -> None:
    # atomic: concurrent fake gh processes never see a half-written file
    tmp = f"{STATE}.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE)


def opt(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def git(*args: str, cwd: str | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode:  # like real gh: pass git's own error through
        sys.stderr.write(res.stderr)
        sys.exit(1)
    return res.stdout


def fail(msg: str, code: int = 1) -> None:
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def find(state: dict, number: str) -> dict:
    for pr in state["prs"]:
        if str(pr["number"]) == str(number):
            return pr
    fail(f"GraphQL: Could not resolve to a PullRequest with the number of {number}.")
    raise AssertionError


def project(pr: dict, args: list[str]) -> dict:
    """Only the fields asked for with --json, like real gh."""
    fields = (opt(args, "--json") or "").split(",")
    return {k: v for k, v in pr.items() if k in fields}


def body_from_stdin(args: list[str]) -> str:
    return sys.stdin.read() if opt(args, "--body-file") == "-" else (opt(args, "--body") or "")


def matches(pr: dict, state: dict, search: str) -> bool:
    me = state.get("me", "me")
    for term in search.split():
        if term == "author:@me" and pr["author"]["login"] != me:
            return False
        if term == "review-requested:@me" and me not in [
            r.get("login") for r in pr.get("reviewRequests", [])
        ]:
            return False
        if term == "assignee:@me" and me not in pr.get("assignees", []):
            return False
        if ":" not in term and term.lower() not in pr["title"].lower():
            return False
    return True


def new_pr(state: dict, head: str, base: str, title: str, *, draft: bool) -> dict:
    n = max([p["number"] for p in state["prs"]] + [0]) + 1
    sha = git("--git-dir", state["bare"], "rev-parse", f"refs/heads/{head}").strip()
    git("--git-dir", state["bare"], "update-ref", f"refs/pull/{n}/head", sha)
    pr = {
        "number": n, "title": title, "author": {"login": state["me"]}, "state": "OPEN",
        "isDraft": draft, "headRefName": head, "baseRefName": base, "headRefOid": sha,
        "additions": 1, "deletions": 0, "changedFiles": 1, "reviewDecision": "REVIEW_REQUIRED",
        "statusCheckRollup": [], "labels": [],
        "url": f"https://github.com/acme/widgets/pull/{n}",
        "updatedAt": "2026-10-20T10:00:00Z", "createdAt": "2026-10-20T10:00:00Z",
        "isCrossRepository": False, "headRepositoryOwner": {"login": "acme"}, "body": "",
        "reviewRequests": [], "latestReviews": [], "comments": [], "assignees": [],
    }  # fmt: skip
    state["prs"].append(pr)
    return pr


def stack_link(state: dict, args: list[str]) -> None:
    """gh stack link [--base B] <stack-number | branch-or-pr>…: push branches, open draft PRs
    with chained bases, make or extend a stack."""
    base = opt(args, "--base") or state["repo"]["defaultBranchRef"]["name"]
    items = [a for i, a in enumerate(args) if not a.startswith("--") and args[i - 1] != "--base"]
    stacks = state.setdefault("stacks", [])
    target = None
    if items and items[0].isdigit() and any(st["number"] == int(items[0]) for st in stacks):
        target = next(st for st in stacks if st["number"] == int(items[0]))
        items = items[1:]
    numbers: list[int] = list(target["prs"]) if target else []
    for item in items:
        pr = next((p for p in state["prs"] if item.isdigit() and p["number"] == int(item)), None)
        if pr is None:  # a branch: push it and open a draft PR on top of the previous one
            git("push", "-q", "origin", f"refs/heads/{item}:refs/heads/{item}")
            prev = find(state, str(numbers[-1]))["headRefName"] if numbers else base
            pr = new_pr(state, item, prev, item, draft=True)
        if pr["number"] not in numbers:
            numbers.append(pr["number"])
    if target is None:
        target = next((st for st in stacks if set(st["prs"]) & set(numbers)), None)
    if target is None:
        target = {"number": 1000 + len(stacks), "base": base, "prs": []}
        stacks.append(target)
    target["prs"] = numbers
    save(state)
    print(f"Stack #{target['number']}: " + " ".join(f"#{n}" for n in numbers))


def stack_merge(state: dict, args: list[str]) -> None:
    """gh stack merge <pr> --yes --merge-method M: merge the PR and everything below it in its
    stack, all or nothing; the base branch on the 'GitHub' repo moves like GitHub's would."""
    number = int(args[0])
    method = opt(args, "--merge-method", "squash")
    stack = next((st for st in state.get("stacks", []) if number in st["prs"]), None)
    if stack is None:
        fail(f"#{number} is not in a stack")
    upto = stack["prs"][: stack["prs"].index(number) + 1]
    prs = [find(state, str(n)) for n in upto]
    for pr in prs:
        if pr["isDraft"] or pr["state"] != "OPEN":
            fail(f"#{pr['number']} can't be merged (draft or not open); nothing was merged")
    bare, base = state["bare"], stack["base"]
    tip = git("--git-dir", bare, "rev-parse", f"refs/heads/{base}").strip()
    for pr in prs:
        head = pr["headRefOid"]
        if method == "squash":
            tree = git("--git-dir", bare, "rev-parse", f"{head}^{{tree}}").strip()
            tip = git(
                "--git-dir", bare, "-c", "user.name=GitHub", "-c", "user.email=gh@example.com",
                "commit-tree", tree, "-p", tip, "-m", f"{pr['title']} (#{pr['number']})",
            ).strip()  # fmt: skip
        else:
            tip = head
        pr["state"] = "MERGED"
    git("--git-dir", bare, "update-ref", f"refs/heads/{base}", tip)
    stack["prs"] = stack["prs"][len(upto) :]
    if stack["prs"]:
        find(state, str(stack["prs"][0]))["baseRefName"] = base
    else:
        state["stacks"].remove(stack)
    save(state)
    print(f"Merged {' '.join(f'#{n}' for n in upto)} into {base}")


def main(argv: list[str]) -> None:
    # one fake gh at a time (the app runs several concurrently): each reads, changes and
    # writes the whole state file
    lock = open(f"{STATE}.lock", "w")  # noqa: SIM115 - held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = load()
    state.setdefault("calls", []).append(argv)
    save(state)
    if argv[:2] == ["extension", "list"]:
        if not state.get("no_stack_extension"):
            print("gh stack\tgithub/gh-stack\tv0.1.0")
        return
    if argv[:2] == ["stack", "link"]:
        stack_link(state, argv[2:])
        return
    if argv[:2] == ["stack", "merge"]:
        stack_merge(state, argv[2:])
        return
    if argv[:2] == ["repo", "view"]:
        print(json.dumps(state["repo"]))
        return
    if argv[:1] == ["pr"]:
        sub, rest = argv[1], argv[2:]
        if sub == "list":
            want = opt(rest, "--state", "open")
            search = opt(rest, "--search", "") or ""
            out = []
            for pr in state["prs"]:
                st = pr["state"]
                if want == "open" and st != "OPEN":
                    continue
                if want == "closed" and st == "OPEN":
                    continue
                if want == "merged" and st != "MERGED":
                    continue
                if opt(rest, "--base") and pr["baseRefName"] != opt(rest, "--base"):
                    continue
                if matches(pr, state, search):
                    out.append(project(pr, rest))
            print(json.dumps(out))
            return
        if sub == "view":
            if rest and not rest[0].startswith("-"):
                print(json.dumps(project(find(state, rest[0]), rest)))
                return
            branch = git("branch", "--show-current").strip()
            for pr in state["prs"]:
                if pr["headRefName"] == branch:
                    print(json.dumps(project(pr, rest)))
                    return
            fail(f'no pull requests found for branch "{branch}"')
        number = rest[0] if rest and not rest[0].startswith("-") else None
        if sub == "checkout":
            pr = find(state, number)
            branch = opt(rest, "--branch") or pr["headRefName"]
            git("fetch", "-q", "origin", f"+refs/pull/{number}/head:refs/heads/{branch}")
            git("switch", "-q", branch)
            return
        if sub == "create":
            head, base = opt(rest, "--head"), opt(rest, "--base")
            n = max([p["number"] for p in state["prs"]] + [0]) + 1
            sha = git("--git-dir", state["bare"], "rev-parse", f"refs/heads/{head}").strip()
            git("--git-dir", state["bare"], "update-ref", f"refs/pull/{n}/head", sha)
            state["prs"].append(
                {
                    "number": n,
                    "title": opt(rest, "--title"),
                    "author": {"login": state["me"]},
                    "state": "OPEN",
                    "isDraft": "--draft" in rest,
                    "headRefName": head,
                    "baseRefName": base,
                    "headRefOid": sha,
                    "additions": 1,
                    "deletions": 0,
                    "changedFiles": 1,
                    "reviewDecision": "REVIEW_REQUIRED",
                    "statusCheckRollup": [],
                    "labels": [],
                    "url": f"https://github.com/acme/widgets/pull/{n}",
                    "updatedAt": "2026-10-20T10:00:00Z",
                    "createdAt": "2026-10-20T10:00:00Z",
                    "isCrossRepository": False,
                    "headRepositoryOwner": {"login": "acme"},
                    "body": body_from_stdin(rest),
                    "reviewRequests": [
                        {"login": r}
                        for r in [rest[i + 1] for i, a in enumerate(rest) if a == "--reviewer"]
                    ],
                    "latestReviews": [],
                    "comments": [],
                }
            )
            save(state)
            print(f"https://github.com/acme/widgets/pull/{n}")
            return
        pr = find(state, number)
        if sub == "review":
            body = body_from_stdin(rest) if "--body-file" in rest else ""
            verdict = (
                "APPROVED"
                if "--approve" in rest
                else "CHANGES_REQUESTED"
                if "--request-changes" in rest
                else "COMMENTED"
            )
            pr.setdefault("latestReviews", []).append(
                {"author": {"login": state["me"]}, "state": verdict, "body": body}
            )
            if verdict != "COMMENTED":
                pr["reviewDecision"] = verdict
        elif sub == "comment":
            pr.setdefault("comments", []).append(
                {"author": {"login": state["me"]}, "body": body_from_stdin(rest)}
            )
        elif sub == "merge":
            if "--auto" in rest:
                pr["autoMergeRequest"] = {"mergeMethod": "SQUASH"}
            else:
                pr["state"] = "MERGED"
        elif sub == "close":
            pr["state"] = "CLOSED"
        elif sub == "reopen":
            pr["state"] = "OPEN"
        elif sub == "edit":
            if opt(rest, "--base"):
                pr["baseRefName"] = opt(rest, "--base")
        elif sub == "ready":
            pr["isDraft"] = "--undo" in rest
        save(state)
        return
    if argv[:2] == ["run", "rerun"]:
        return
    if argv[:2] == ["api", "graphql"]:
        fields = dict(a.split("=", 1) for a in argv if "=" in a and not a.startswith("query="))
        query = next(a for a in argv if a.startswith("query="))
        if "resolveReviewThread" in query or "unresolveReviewThread" in query:
            resolved = query.startswith("query=mutation($id: ID!) { resolve")
            for threads in state.get("threads", {}).values():
                for t in threads:
                    if t["id"] == fields["id"]:
                        t["isResolved"] = resolved
            save(state)
            print("{}")
            return
        if "stack { entries" in query:  # GitHub.stack_members
            n = int(fields["number"])
            stack = next((st for st in state.get("stacks", []) if n in st["prs"]), None)
            nodes = [
                {"position": i + 1, "pullRequest": {"number": m}}
                for i, m in enumerate(stack["prs"] if stack else [])
            ]
            pr = {"stack": stack and {"entries": {"nodes": nodes}}}
            print(json.dumps({"data": {"repository": {"pullRequest": pr}}}))
            return
        if "statusCheckRollup" in query:  # GitHub.statuses: aliased p<N> fields
            repo = {}
            for n in re.findall(r"p(\d+): pullRequest", query):
                pr = next((p for p in state["prs"] if str(p["number"]) == n), None)
                if pr is None:
                    continue
                concl = {c.get("conclusion") or c.get("status") for c in pr["statusCheckRollup"]}
                rollup = (
                    None
                    if not concl
                    else "FAILURE"
                    if "FAILURE" in concl
                    else "PENDING"
                    if concl - {"SUCCESS", "SKIPPED", "NEUTRAL"}
                    else "SUCCESS"
                )
                stack = next((st for st in state.get("stacks", []) if int(n) in st["prs"]), None)
                repo[f"p{n}"] = {
                    "stack": stack
                    and {
                        "number": stack["number"],
                        "size": len(stack["prs"]),
                        "baseRefName": stack["base"],
                    },
                    "stackEntry": stack and {"position": stack["prs"].index(int(n)) + 1},
                    "reviewDecision": pr.get("reviewDecision"),
                    "commits": {
                        "nodes": [{"commit": {"statusCheckRollup": rollup and {"state": rollup}}}]
                    },
                }
            print(json.dumps({"data": {"repository": repo}}))
            return
        threads = state.get("threads", {}).get(fields.get("number", ""), [])
        print(
            json.dumps(
                {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": threads}}}}}
            )
        )
        return
    if argv[:1] == ["api"]:
        path = argv[1]
        fields = dict(a.split("=", 1) for a in argv[2:] if "=" in a)
        parts = path.split("/")
        number = parts[4]
        threads = state.setdefault("threads", {}).setdefault(number, [])
        if path.endswith("/replies"):
            cid = int(parts[6])
            for t in threads:
                if any(c["databaseId"] == cid for c in t["comments"]["nodes"]):
                    t["comments"]["nodes"].append(
                        {
                            "id": f"c{len(t['comments']['nodes']) + 100}",
                            "databaseId": cid + 100,
                            "author": {"login": state["me"]},
                            "body": fields["body"],
                        }
                    )
        else:
            n = sum(len(t["comments"]["nodes"]) for t in threads) + 1
            threads.append(
                {
                    "id": f"T{len(threads) + 1}",
                    "isResolved": False,
                    "isOutdated": False,
                    "path": fields["path"],
                    "line": int(fields["line"]),
                    "diffSide": fields["side"],
                    "comments": {
                        "nodes": [
                            {
                                "id": f"C{n}",
                                "databaseId": 1000 + n,
                                "author": {"login": state["me"]},
                                "body": fields["body"],
                            }
                        ]
                    },
                }
            )
        save(state)
        print("{}")
        return
    fail(f"fake gh: unsupported command {argv}", 2)


if __name__ == "__main__":
    main(sys.argv[1:])
