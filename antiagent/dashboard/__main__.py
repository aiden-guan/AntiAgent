"""Entrypoint for python -m antiagent.dashboard."""

from __future__ import annotations

import sys
from antiagent.dashboard.server import parse_dashboard_args, run_dashboard

if __name__ == "__main__":
    args = parse_dashboard_args(sys.argv[1:])
    run_dashboard(
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        workspace_path=args.workspace,
    )
