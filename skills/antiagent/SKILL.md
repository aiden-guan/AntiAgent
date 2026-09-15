---
name: antiagent
description: Supervised execution and "Approved for Me" safety guard for Antigravity. Use when reviewing command safety, verifying workspace confinement, auditing tool calls, or configuring AntiAgent policies.
---

# AntiAgent Safety Supervisor Skill

AntiAgent provides an intelligent, supervised execution layer for Google Antigravity. It auto-approves safe and routine development commands while intercepting risky or destructive operations before execution.

## Core Capabilities

1. **"Approved for Me" Execution**:
   - Routine file reads, directory listings, tests, linter checks, and standard git status commands are pre-approved instantly with zero friction.
   - Destructive commands (`rm -rf`, `git reset --hard`, `git push --force`) are intercepted for explicit user approval.
   - Catastrophic commands (privilege escalation, system directory deletion, unvetted script piping) are hard-blocked.

2. **Native Subagent Supervisor (Zero API Key Needed)**:
   - When AntiAgent is running in Native Mode, no external Gemini or OpenAI API keys are required.
   - Antigravity's own session and intelligent heuristics perform all necessary reviews.

3. **CLI Management**:
   - `antiagent status`: View hook status and current safety profile.
   - `antiagent test`: Run the simulated tool execution matrix.
   - `antiagent audit`: View real-time audit logs of all intercepted tool calls.
   - `antiagent dashboard`: Launch the interactive visual web dashboard.
