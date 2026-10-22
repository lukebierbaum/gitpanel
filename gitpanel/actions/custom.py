"""User-defined commands from `[[commands]]` in config.toml.

These run arbitrary shell commands with `sh -c` in the active worktree — the
one place outside `Git.run` that spawns processes (see CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .context import Scope
from .registry import Action, ActionEnv, ActionError

CONTEXTS = {
    "global": frozenset({Scope.GLOBAL}),
    "commit": frozenset({Scope.COMMIT}),
    "branch": frozenset({Scope.BRANCH}),
    "file": frozenset({Scope.FILE}),
    "worktree": frozenset({Scope.WORKTREE}),
}
NEEDS = {
    "commit": lambda c: bool(c.commits),
    "branch": lambda c: bool(c.refs),
    "file": lambda c: bool(c.files),
    "worktree": lambda c: c.worktree is not None,
}


class _Quoted(dict[str, str]):
    def __missing__(self, key: str) -> str:
        raise ActionError(f"unknown placeholder {{{key}}}")


def render(template: str, values: Mapping[str, str]) -> str:
    """Substitute {placeholders}, shell-quoting every value (multi-value ones per item)."""
    quoted = _Quoted()
    for k, v in values.items():
        if k in ("commits", "files"):
            quoted[k] = " ".join(shlex.quote(x) for x in v.split(" ") if x)
        else:
            quoted[k] = shlex.quote(v) if v else "''"
    return template.format_map(quoted)


async def _spawn(cmd: str, cwd: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_shell(
        cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )


def compile_command(spec: Mapping[str, Any]) -> Action:
    cid = str(spec.get("id", "")).strip()
    run_tpl = str(spec.get("run", "")).strip()
    if not cid or not run_tpl:
        raise ValueError("custom command needs `id` and `run`")
    context = str(spec.get("context", "global"))
    if context not in CONTEXTS:
        raise ValueError(f"custom.{cid}: context must be one of {', '.join(CONTEXTS)}")
    output = str(spec.get("output", "none"))
    if output not in ("none", "popup", "terminal"):
        raise ValueError(f"custom.{cid}: output must be none, popup or terminal")
    prompts = [p for p in spec.get("prompts", []) if isinstance(p, dict) and p.get("name")]
    label = str(spec.get("label") or cid)
    read_only = bool(spec.get("read_only", False))

    async def run(env: ActionEnv) -> None:
        values = env.ctx.placeholders()
        for p in prompts:
            answer = await env.ui.prompt(
                str(p.get("label", p["name"])), default=str(p.get("default", ""))
            )
            if answer is None:
                return
            values[str(p["name"])] = answer
        cmd = render(run_tpl, values)
        cwd = str(env.session.active.path)
        if output == "terminal":
            code = await env.ui.run_in_terminal(["sh", "-c", cmd], env.session.active.path)
            if code:
                env.ui.notify(f"{label} exited with {code}", severity="warning")
            return
        proc = await _spawn(cmd, cwd)
        assert proc.stdout is not None
        if output == "popup":

            async def lines() -> AsyncIterator[str]:
                assert proc.stdout is not None
                async for raw in proc.stdout:
                    yield raw.decode("utf-8", "replace").rstrip("\n")
                await proc.wait()

            await env.ui.show_output(f"$ {cmd}", lines())
            return
        out = (await proc.stdout.read()).decode("utf-8", "replace")
        code = await proc.wait()
        if code:
            env.ui.notify(f"{label} failed ({code})", severity="error")
            env.ui.show_error(label, f"$ {cmd}\n{out}")
        else:
            env.ui.notify(out.strip().splitlines()[-1] if out.strip() else f"{label}: done")

    needs = NEEDS.get(context)
    return Action(
        id=f"custom.{cid}",
        label=label,
        run=run,
        scopes=CONTEXTS[context],
        when=(lambda c, n=needs: n(c)) if needs else (lambda c: True),
        key=str(spec["key"]) if spec.get("key") else None,
        group="Custom commands",
        mutating=not read_only,
        confirm=str(spec["confirm"]) if spec.get("confirm") else None,
        description=run_tpl,
    )


def compile_all(specs: list[Mapping[str, Any]]) -> tuple[list[Action], list[str]]:
    actions: list[Action] = []
    errors: list[str] = []
    for spec in specs:
        try:
            actions.append(compile_command(spec))
        except (ValueError, KeyError) as e:
            errors.append(str(e))
    return actions, errors


def register_custom(registry: Any, specs: list[Mapping[str, Any]]) -> list[str]:
    actions, errors = compile_all(specs)
    for a in actions:
        try:
            registry.register(a)
        except ValueError as e:
            errors.append(str(e))
    return errors
