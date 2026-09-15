# Contributing to AntiAgent

We welcome contributions to AntiAgent! Whether you're adding new heuristic security rules, integrating new LLM providers, or polishing the CLI experience, here is how you can help.

## Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/aidenguan/AntiAgent.git
   cd AntiAgent
   ```

2. **Run tests:**
   ```bash
   python3 -m unittest discover -s tests
   ```

3. **Install locally in editable mode:**
   ```bash
   pip install -e .
   ```

## Architecture Overview

- `antiagent/hook.py`: The Antigravity lifecycle hook entrypoint (`PreToolUse`).
- `antiagent/engine/evaluator.py`: Orchestrator coordinating Tier 1 heuristics and Tier 2 LLM supervision.
- `antiagent/engine/heuristics/`: Fast deterministic AST and regex guards:
  - `fs_guard.py`: Workspace boundary & sensitive files (`.env`, `~/.ssh`).
  - `command_guard.py`: Shell command inspection and hard-deny rules.
  - `git_guard.py`: Destructive Git command protection (`push --force`, `reset --hard`).
- `antiagent/engine/supervisor/`:
  - `reviewer.py`: AI supervisor client (Gemini Flash, OpenAI, Ollama).
  - `cache.py`: Decision cache for performance.
- `antiagent/cli.py`: User-facing CLI (`antiagent install`, `antiagent test`, `antiagent audit`).

## Security Disclosures

If you discover a security bypass in AntiAgent's guards or hooks, please report it privately to the maintainers before opening a public issue.
