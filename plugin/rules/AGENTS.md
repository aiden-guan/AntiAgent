# AntiAgent Active Supervision Rules

AntiAgent is actively monitoring this workspace via lifecycle hooks (`PreToolUse`).

- Safe development operations (reading files, listing directories, running tests, linting, git status, git diff, gh pr status, gh pr checks) are automatically pre-approved ("Approved for Me").
- Destructive actions (e.g. `rm -rf`, `git reset --hard`, `git push --force`) or actions modifying files outside the workspace are gated and will request explicit user confirmation.
- Catastrophic commands (privilege escalation, system directory deletion, unvetted script piping) are blocked immediately.
- Pull request monitoring (Claude Code style) is supported: use `antiagent pr monitor` or `antiagent pr autofix` to track CI check runs and extract step failure logs for automated fixes.
