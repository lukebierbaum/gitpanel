"""A stand-in for the `gh` CLI used by the tests (no network).

State lives in the JSON file named by $FAKE_GH_STATE:
  {"repo": {...}, "bare": "<path of the 'GitHub' bare repo>", "me": "me",
   "prs": [<gh --json style PR dicts>], "threads": {"<n>": [<thread nodes>]}, "calls": [[args]]}
Git operations (checkout, create) act on real repositories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

STATE = os.environ["FAKE_GH_STATE"]


def load() -> dict:
    with open(STATE) as f:
        return json.load(f)


def save(state: dict) -> None:
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)


def opt(args: list[str], name: str, default: str | None = None) -> str | None:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else default
    return default


def git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def fail(msg: str, code: int = 1) -> None:
    sys.stderr.write(msg + "\n")
    sys.exit(code)


def find(state: dict, number: str) -> dict:
    for pr in state["prs"]:
        if str(pr["number"]) == str(number):
            return pr
    fail(f"GraphQL: Could not resolve to a PullRequest with the number of {number}.")
    raise AssertionError


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


def main(argv: list[str]) -> None:
    state = load()
    state.setdefault("calls", []).append(argv)
    save(state)
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
                if matches(pr, state, search):
                    out.append(pr)
            print(json.dumps(out))
            return
        if sub == "view":
            if rest and not rest[0].startswith("-"):
                print(json.dumps(find(state, rest[0])))
                return
            branch = git("branch", "--show-current").strip()
            for pr in state["prs"]:
                if pr["headRefName"] == branch:
                    print(json.dumps(pr))
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
