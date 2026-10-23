from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gitpanel.ui.fmt import ago, ahead_behind, branch_or_detached, fmt_duration, plural


def test_formatting_helpers():
    assert plural(1, "file") == "1 file" and plural(3, "file") == "3 files"
    assert plural(2, "entry", "entries") == "2 entries"
    assert fmt_duration(0.012) == "12 ms" and fmt_duration(3.42) == "3.4 s"
    assert fmt_duration(125) == "2m05s" and fmt_duration(None) == ""
    assert ago(datetime.now(UTC) - timedelta(hours=5, minutes=3)) == "5h"
    assert ago(datetime.now(UTC)) == "now" and ago(None) == ""
    assert ahead_behind(2, 0) == "↑2 ↓0"
    assert branch_or_detached("main", "abc") == "main"
    assert branch_or_detached(None, "0123456789") == "detached @ 01234567"
