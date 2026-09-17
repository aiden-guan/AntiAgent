"""Direct execution module entrypoint for python -m antiagent."""

import sys

# Ensure UTF-8 output streams on Windows to prevent charmap/CP1252 emoji encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

from antiagent.cli import main

if __name__ == "__main__":
    main()
