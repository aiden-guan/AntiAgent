"""Entrypoint for python -m antiagent.dashboard."""
import sys
from antiagent.dashboard.server import run_dashboard

if __name__ == "__main__":
    open_browser = "--no-open" not in sys.argv
    run_dashboard(open_browser=open_browser)
