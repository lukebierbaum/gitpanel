"""Merge-conflict actions: open the resolver, accept whole files, per-chunk resolution."""

from __future__ import annotations

from ...git import conflicts as CF
from ..context import Scope, SelectionContext
from ..registry import Action, ActionEnv, ActionError

RESOLVER = frozenset({"resolver"})
COMMIT_SCREEN = frozenset({"commit"})
FILE = frozenset({Scope.FILE})
CONFLICT = frozenset({Scope.CONFLICT})


def _conflict_files(ctx: SelectionContext) -> list[str]:
    return [f.path for f in ctx.files if f.group == "conflict"]


async def _open(env: ActionEnv) -> None:
    paths = _conflict_files(env.ctx)
    env.ui.open_screen("resolver", path=paths[0])


async def _accept(env: ActionEnv, side: str) -> None:
    paths = _conflict_files(env.ctx)
    await CF.accept(env.session.git, paths, side)  # type: ignore[arg-type]
    if env.entry:
        env.entry.op = f"Accept {side} for {', '.join(paths)}"
    env.ui.notify(f"Accepted {side} for {len(paths)} file{'s' if len(paths) != 1 else ''}")


async def _accept_ours(env: ActionEnv) -> None:
    await _accept(env, "ours")


async def _accept_theirs(env: ActionEnv) -> None:
    await _accept(env, "theirs")


# -- resolver (operates on the MergeModel published in ctx.form) ----------------------------


def _chunk(ctx: SelectionContext) -> CF.Chunk | None:
    model: CF.MergeModel | None = ctx.form.get("merge")
    idx = ctx.form.get("chunk")
    if model is None or idx is None or not (0 <= idx < len(model.chunks)):
        return None
    return model.chunks[idx]


def _has_chunk(ctx: SelectionContext) -> bool:
    return _chunk(ctx) is not None


def _set(resolution: CF.Resolution):  # type: ignore[no-untyped-def]
    async def run(env: ActionEnv) -> None:
        chunk = _chunk(env.ctx)
        assert chunk is not None
        chunk.resolution = resolution
        env.ui.post("resolver.changed", advance=True)

    return run


async def _edit(env: ActionEnv) -> None:
    chunk = _chunk(env.ctx)
    assert chunk is not None
    current = chunk.lines()
    initial = "".join(current if current is not None else chunk.ours + chunk.theirs)
    text = await env.ui.prompt("Edit the merged lines", default=initial, multiline=True)
    if text is None:
        return
    chunk.custom = text.splitlines(keepends=True)
    if chunk.custom and not chunk.custom[-1].endswith("\n"):
        chunk.custom[-1] += "\n"
    chunk.resolution = "custom"
    env.ui.post("resolver.changed", advance=True)


async def _reset(env: ActionEnv) -> None:
    chunk = _chunk(env.ctx)
    assert chunk is not None
    chunk.resolution = None
    env.ui.post("resolver.changed", advance=False)


async def _apply(env: ActionEnv) -> None:
    model: CF.MergeModel | None = env.ctx.form.get("merge")
    if model is None:
        raise ActionError("Nothing to apply")
    if model.unresolved and not await env.ui.confirm(
        "Unresolved conflicts",
        f"{model.unresolved} chunk(s) are unresolved and will keep conflict markers.\n"
        "Save and mark the file as resolved anyway?",
        danger=True,
    ):
        return
    await CF.resolve_with(env.session.git, model.path, model.result())
    if env.entry:
        env.entry.op = f"Resolve {model.path}"
    env.ui.notify(f"Resolved {model.path}")
    env.ui.post("resolver.done")


def actions() -> list[Action]:
    def r(id_, label, run, key, mutating=False, **kw):  # type: ignore[no-untyped-def]
        return Action(
            id_,
            label,
            run,
            scopes=CONFLICT,
            when=_has_chunk,
            key=key,
            group="Resolve",
            screens=RESOLVER,
            mutating=mutating,
            **kw,
        )

    return [
        Action(
            "conflict.resolve",
            "Resolve Conflicts…",
            _open,
            scopes=FILE,
            when=lambda c: bool(_conflict_files(c)),
            key="enter",
            group="Conflicts",
            screens=COMMIT_SCREEN,
        ),
        Action(
            "conflict.accept_ours",
            "Accept Yours",
            _accept_ours,
            scopes=FILE,
            when=lambda c: bool(_conflict_files(c)),
            key="alt+left",
            group="Conflicts",
            screens=COMMIT_SCREEN,
            mutating=True,
            destructive=True,
            paths=_conflict_files,
        ),
        Action(
            "conflict.accept_theirs",
            "Accept Theirs",
            _accept_theirs,
            scopes=FILE,
            when=lambda c: bool(_conflict_files(c)),
            key="alt+right",
            group="Conflicts",
            screens=COMMIT_SCREEN,
            mutating=True,
            destructive=True,
            paths=_conflict_files,
        ),
        r("resolver.take_ours", "Take left (ours)", _set("ours"), "left"),
        r("resolver.take_theirs", "Take right (theirs)", _set("theirs"), "right"),
        r("resolver.take_both", "Take both (left first)", _set("both"), "b"),
        r("resolver.take_both_reversed", "Take both (right first)", _set("both_reversed"), "B"),
        r("resolver.edit", "Edit result…", _edit, "e"),
        r("resolver.reset", "Reset chunk", _reset, "u"),
        Action(
            "resolver.apply",
            "Apply and Mark Resolved",
            _apply,
            when=lambda c: c.form.get("merge") is not None,
            key="ctrl+s",
            group="Resolve",
            screens=RESOLVER,
            mutating=True,
        ),
    ]
