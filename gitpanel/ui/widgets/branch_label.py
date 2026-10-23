"""How a branch or tag is drawn in lists (Branches popup, the Log's branch panel)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text

from ...git import refs as R
from ...git.refs import BranchRef
from ...git.session import RepoSession
from ..colors import C
from ..fmt import ahead_behind


def load_favorites(session: RepoSession) -> set[str]:
    return R.Favorites(session.private_dir(shared=True) / "favorites.json").load()


def favorites_first(favorites: set[str]) -> Callable[[Any], tuple[bool, str]]:
    """Sort key: favourites first, then by name."""
    return lambda ref: (ref.name not in favorites, ref.short.lower())


def branch_label(
    ref: Any,
    *,
    current: str | None,
    favorites: set[str],
    active: Path | None,
    has_remotes: bool = False,
    compact: bool = False,
    strip: str = "",
) -> Text:
    """★ favourite · ● current · name · ⎇ worktree · ↑↓ · upstream state.

    `compact` (the narrow branch panel) drops the worktree name and the upstream name;
    `strip` removes a folder prefix from the name.
    """
    t = Text()
    is_current = ref.name == current
    # a space after each symbol: fonts often draw ★ ● wider than one cell
    t.append("★ " if ref.name in favorites else "  ", style=C.favorite)
    t.append("● " if is_current else "  ", style=C.current)
    t.append(ref.short.removeprefix(strip), style=f"bold {C.current}" if is_current else "")
    if not isinstance(ref, BranchRef):  # tag
        return t
    sep = " " if compact else "  "
    wt = ref.worktree
    if wt is not None and wt.resolve() != active:
        t.append(f"{sep}⎇" if compact else f"{sep}⎇ {wt.name}", style=f"italic {C.worktree}")
    if ref.ahead or ref.behind:
        t.append(f"{sep}{ahead_behind(ref.ahead, ref.behind)}", style="dim" if compact else C.other)
    if ref.gone:
        t.append(f"{sep}upstream gone", style="dim italic")
    elif ref.is_local and ref.upstream is None and has_remotes:
        t.append(f"{sep}local", style=C.unpushed)  # never pushed / no upstream
    elif ref.upstream_short and not compact:
        t.append(f"{sep}→ {ref.upstream_short}", style="dim")
    return t
