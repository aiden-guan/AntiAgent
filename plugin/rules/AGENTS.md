# AntiAgent Active Supervision Rules

AntiAgent is actively monitoring this workspace via lifecycle hooks (`PreToolUse`).

- Safe development operations (reading files, listing directories, running tests, linting, git status, git diff) are automatically pre-approved ("Approved for Me").
- Destructive actions (e.g. `rm -rf`, `git reset --hard`, `git push --force`) or actions modifying files outside the workspace are gated and will request explicit user confirmation.
- Catastrophic commands (privilege escalation, system directory deletion, unvetted script piping) are blocked immediately.
