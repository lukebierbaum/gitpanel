"""Browser UI (prototype): an aiohttp server over the same actions/git/forge layers as the TUI.

`server.py` serves the page and one WebSocket per tab; `bridge.py` implements the `Ui`
protocol over that socket; `serialize.py` turns backend models into JSON. The front end
is plain HTML/CSS/JS in `static/` (no build step). Needs the `web` extra (aiohttp).
"""
