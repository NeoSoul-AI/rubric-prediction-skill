#!/usr/bin/env python3
"""
Compatibility entry point. Implementation lives in rubric_forecast.engine.
Inserts the repository root on sys.path so this file works without pip install.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from rubric_forecast.engine import main

if __name__ == "__main__":
    raise SystemExit(main())
