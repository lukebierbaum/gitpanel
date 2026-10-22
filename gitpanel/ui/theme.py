"""gitpanel's default colour scheme: Hulk — gamma-ray green on purple shorts."""

from __future__ import annotations

from textual.theme import Theme

HULK_GREEN = "#6CCB3A"
HULK_GREEN_DEEP = "#3E8E2A"
HULK_PURPLE = "#8B45C8"
HULK_PURPLE_DEEP = "#5B2A86"

HULK = Theme(
    name="hulk",
    primary=HULK_PURPLE,
    secondary=HULK_GREEN_DEEP,
    accent=HULK_GREEN,
    success=HULK_GREEN,
    warning="#E3B23C",
    error="#E5484D",
    foreground="#E6EDE3",
    background="#101410",
    surface="#151B15",
    panel="#241B30",
    dark=True,
    variables={
        "footer-key-foreground": HULK_GREEN,
        "block-cursor-background": HULK_PURPLE,
        "input-selection-background": f"{HULK_PURPLE} 40%",
        "scrollbar": HULK_PURPLE_DEEP,
        "scrollbar-hover": HULK_PURPLE,
        "scrollbar-active": HULK_GREEN,
        "border": HULK_PURPLE,
    },
)

HULK_LIGHT = Theme(
    name="hulk-light",
    primary="#6A2FA0",
    secondary="#2F7A1F",
    accent="#3E9A24",
    success="#2F8A1F",
    warning="#B07A00",
    error="#C62828",
    foreground="#1C1A22",
    background="#F4F8F1",
    surface="#FFFFFF",
    panel="#ECE3F5",
    dark=False,
)

THEMES = (HULK, HULK_LIGHT)
DEFAULT_THEME = "hulk"
