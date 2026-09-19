from __future__ import annotations

import sys

from core.watchdog import run_watchdog


if __name__ == "__main__":
    raise SystemExit(run_watchdog(sys.argv[1:]))
