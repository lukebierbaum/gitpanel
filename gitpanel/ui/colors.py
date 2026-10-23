"""Every colour the UI draws with, in one place, for the dark and the light theme.

Use `C.<name>` at render time (not at import time) so the current theme's value is picked
up: `Text("ok", style=C.ok)`. Names are semantic — `C.added`, `C.unpushed`, `C.fail` —
with the raw hues (`C.green` …) for the few places that are about colour itself.
Icon tables (`CI_ICON`, `CHECK_ICON`) hold colour *names*; resolve them with `C[name]`.
"""

from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Palette:
    green: str
    purple: str
    amber: str
    red: str
    teal: str
    grey: str
    lilac: str
    blue: str
    pink: str
    ink: str  # text on a coloured badge
    lanes: tuple[str, ...]  # graph lane colours (no amber/purple: they mark unpushed/remote)

    # semantic names → hue
    @property
    def added(self) -> str:
        return self.green

    @property
    def removed(self) -> str:
        return self.red

    @property
    def modified(self) -> str:
        return self.lilac

    @property
    def renamed(self) -> str:
        return self.teal

    @property
    def ok(self) -> str:
        return self.green

    @property
    def fail(self) -> str:
        return self.red

    @property
    def pending(self) -> str:
        return self.amber

    @property
    def warn(self) -> str:
        return self.amber

    @property
    def muted(self) -> str:
        return self.grey

    @property
    def current(self) -> str:  # the checked-out branch, the active worktree, "ours"
        return self.green

    @property
    def other(self) -> str:  # remotes, "theirs", section titles
        return self.purple

    @property
    def unpushed(self) -> str:
        return self.amber

    @property
    def incoming(self) -> str:
        return self.purple

    @property
    def worktree(self) -> str:
        return self.teal

    @property
    def label(self) -> str:  # PR labels, tags in lists
        return self.teal

    @property
    def sha(self) -> str:
        return self.green

    @property
    def favorite(self) -> str:
        return self.amber


DARK = Palette(
    green="#6CCB3A",
    purple="#A66BE0",
    amber="#E3B23C",
    red="#E5484D",
    teal="#3FB8AF",
    grey="#8A8F87",
    lilac="#B58CE8",
    blue="#4FA3E0",
    pink="#E06C9F",
    ink="#101410",
    lanes=("#6CCB3A", "#3FB8AF", "#4FA3E0", "#E06C9F", "#9BDB4D", "#7FDBCA", "#5FB0E8"),
)  # fmt: skip

LIGHT = Palette(
    green="#2F8A1F",
    purple="#6A2FA0",
    amber="#9A6A00",
    red="#C62828",
    teal="#00796B",
    grey="#6B6F68",
    lilac="#7B4BB0",
    blue="#1F6FB2",
    pink="#B0306A",
    ink="#FFFFFF",
    lanes=("#2F8A1F", "#00796B", "#1F6FB2", "#B0306A", "#5A8A00", "#00897B", "#1565C0"),
)  # fmt: skip

_HUES = {f.name for f in fields(Palette)} - {"lanes"}


class _Colors:
    """The current palette; switched by the app when the theme changes."""

    palette: Palette = DARK

    def __getattr__(self, name: str) -> str:
        return getattr(self.palette, name)

    def __getitem__(self, name: str) -> str:
        """A colour by name (for tables of names); unknown names pass through unchanged."""
        if name in _HUES or hasattr(Palette, name):
            return getattr(self.palette, name)
        return name

    def use(self, dark: bool) -> None:
        self.palette = DARK if dark else LIGHT


C = _Colors()

# icon, colour name
CI_ICON: dict[str, tuple[str, str]] = {
    "success": ("✓", "ok"),
    "failure": ("✗", "fail"),
    "pending": ("●", "pending"),
    "none": ("·", "muted"),
}
CHECK_ICON: dict[str, tuple[str, str]] = {
    **CI_ICON,
    "skipped": ("–", "muted"),
    "neutral": ("○", "muted"),
    "cancelled": ("⊘", "muted"),
}
# file status letter → colour name
STATUS_COLOR = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "renamed"}
