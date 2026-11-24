"""Standalone entry point for AudioFutbolServer.exe (no console window).

Run it next to the portable game.  Settings live in ``servidor/config.json``;
logs, friends, blocks and restart history are written to ``servidor/``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from audiofutbol.net.server import main  # noqa: E402  (path setup must run first)

if __name__ == "__main__":
    raise SystemExit(main())
