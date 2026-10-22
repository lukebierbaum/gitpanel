"""Key normalisation.

Keyboard layouts that make Alt behave like the Mac Option key (e.g. "English
(Macintosh)") turn Alt+key into a special character before the terminal sees
it: Alt+5 arrives as "∞", Alt+h as "˙". When such a character isn't claimed by
anything else, gitpanel treats it as the alt+ key it came from.

Option+e/i/n/u/` are dead keys on that layout (they wait for a second key), so
alt+e/i/n/u can't be recovered this way; those actions stay reachable through
the palette (ctrl+p) and the actions menu.
"""

from __future__ import annotations

# macOS US layout: Option+key → character
MAC_OPTION: dict[str, str] = {
    # digits
    "¡": "alt+1", "™": "alt+2", "£": "alt+3", "¢": "alt+4", "∞": "alt+5",
    "§": "alt+6", "¶": "alt+7", "•": "alt+8", "ª": "alt+9", "º": "alt+0",
    # letters (no e, i, n, u: dead keys)
    "å": "alt+a", "∫": "alt+b", "ç": "alt+c", "∂": "alt+d", "ƒ": "alt+f",
    "©": "alt+g", "˙": "alt+h", "∆": "alt+j", "˚": "alt+k", "¬": "alt+l",
    "µ": "alt+m", "ø": "alt+o", "π": "alt+p", "œ": "alt+q", "®": "alt+r",
    "ß": "alt+s", "†": "alt+t", "√": "alt+v", "∑": "alt+w", "≈": "alt+x",
    "¥": "alt+y", "Ω": "alt+z",
    # punctuation
    "–": "alt+minus", "≠": "alt+equals_sign", "“": "alt+left_square_bracket",
    "‘": "alt+right_square_bracket", "…": "alt+semicolon", "≤": "alt+comma",
    "≥": "alt+full_stop", "÷": "alt+slash",
}  # fmt: skip


def alt_equivalent(character: str | None) -> str | None:
    """The alt+ key a Mac-Option-style layout turned into `character`, if any."""
    if not character:
        return None
    return MAC_OPTION.get(character)
