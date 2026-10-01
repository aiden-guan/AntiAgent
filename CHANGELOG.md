# Changelog

All notable changes to the **AntiAgent** project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v0.4.2] — 2026-09-30

### Summary
Introduced **Antigravity Conversation Artifact & Brain Directory Auto-Approval (Closes #3)**. AntiAgent now treats Antigravity's current per-conversation `artifactDirectoryPath` as an agent-owned write area. Routine file mutations (`write_to_file`, `replace_file_content`, `delete_file`) targeting inside that directory are deterministically auto-approved with zero confirmation prompts and without disrupting prompt queue interaction states. Sibling conversations, sensitive files (`.env`, credentials, system roots), and external filesystem paths remain strictly protected under workspace boundary guards.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Runtime Trusted Artifact Containment (`engine/heuristics/fs_guard.py`)** | Added canonical-path containment verification (`_is_inside_roots`, `_is_path_contained`) for trusted artifact paths distinct from workspace roots. Correctly rejects path traversal (`conv-a/../conv-b`), prefix collisions (`conv-a-malicious`), and POSIX symlink escapes while preserving Windows case-insensitivity. |
| **Deterministic Heuristic Auto-Approval (`engine/heuristics/fs_guard.py`)** | Evaluates file mutation tools (`write_to_file`, `replace_file_content`, `delete_file`) targeting inside `trusted_artifact_paths` immediately after sensitive target checks, returning `DECISION_ALLOW` deterministically before outside-workspace rules and before LLM supervisor invocation. |
| **Antigravity PreToolUse Hook Integration (`hook.py`)** | Authoritatively passes `artifactDirectoryPath` from Antigravity's runtime payload into `AntiAgentEvaluator` and `FSGuard`. Ensures interaction state machine stays in `RUNNING` with `pending_approval = False` on auto-approved artifact mutations. |
| **Safety Invariant Prioritization** | Strict evaluation hierarchy guarantees that credential files (`.env`, SSH keys, SAM, registry hives) and protected system paths (`/etc`, `/System`, `/Windows`) are checked FIRST and always flagged for confirmation, even if placed inside an artifact directory. |
| **User & Environment Control (`config.py`)** | Added `auto_approve_artifact_writes: bool = True` with environment override `ANTIAGENT_AUTO_APPROVE_ARTIFACT_WRITES`. Untrusted repositories cannot declare arbitrary write roots via `.antiagent.json`. |

### Verification Proof
- All 323 unit tests passed (`python3 -m unittest discover tests`) with 0 failures and 0 errors.
- Added comprehensive unit, evaluator, hook contract, and Windows path containment test suite in `tests/test_artifact_directory.py` (18 tests).
- Verified sensitive target check precedence, traversal rejection, sibling conversation isolation, and symlink escape rejection.
- Closes GitHub Issue [#3](https://github.com/aiden-guan/AntiAgent/issues/3).

---

## [v0.4.1] — 2026-09-28

### Summary
Comprehensive **Dashboard UI Revamp, High-Telemetry System HUD, View Mode Workspace Navigation, Card Accordion / Focus Engine, Dynamic Release Notes Resolution, and Layout Customization**. Solves excessive vertical scrolling and visual clutter by transforming the AntiAgent Dashboard into an organized, responsive mission control center. Resolves the update dialog showing generic installer boilerplate instead of actual release notes by automatically extracting feature highlights from the changelog locally or remotely.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Telemetry HUD Strip (`telemetry-hud`)** | 5-tile responsive glassmorphic telemetry bar providing instant bird's-eye metrics without vertical scrolling: Safety Stance & Hooks, Real-Time Audit Intercepts & Denied count, Agent Sessions count, PR & CI Guard status, and Remote Fleet machine count. Clicking any tile jumps directly to its dedicated view. |
| **Workspace View Navigation (`view-tabs`)** | High-performance segmented tab navigation dividing tools into 7 focused workspaces: `Overview [1]`, `Live Audit [2]`, `Agent Sessions [3]`, `Security & Rules [4]`, `Safety Sandbox [5]`, `Fleet & PRs [6]`, and `All Cards [7]`. Includes URL hash synchronization (`#overview`, `#audit`, etc.) and instant keyboard navigation (`1-7`). |
| **Standardized Card Accordion & Collapse System** | Every dashboard card features an interactive header bar with one-click accordion collapsing. When collapsed, cards compress to a 44px compact bar displaying a dynamic summary badge (e.g. `Balanced • Active`, `24 events • 0 blocked`, `0 machines`), saving 80% vertical space while retaining live state awareness. Card collapse states are permanently remembered via `localStorage`. |
| **Distraction-Free Fullscreen Focus Mode (`⛶`)** | Maximize any card into an immersive, viewport-filling Focus Mode with blurred glass backdrop, generous spacing, dedicated top exit ribbon, and full keyboard control (`Esc` to dismiss). Ideal for reading deep conversation transcripts, inspecting live audit JSON payloads, or running threat simulations. |
| **Global Layout & Card Customization Modal** | Added `#customizeLayoutModal` allowing users to toggle individual card visibility (e.g. hide SSH remotes or GitHub PRs when not needed), select default landing views, choose UI spacing density (Comfortable vs Compact for laptops), and reset to defaults. Persisted across sessions via `localStorage`. |
| **Overview Quick Actions Ribbon** | Sleek action ribbon on the Overview tab providing instant one-click shortcuts to Run System Doctor, Test Threat Simulation, Inspect Live Tool Calls Sidebar, and Check for Updates. |
| **Instant Search & Jump Filter (`Cmd+K`)** | Dedicated search box filtering cards and expanding matching content in real time. Bound to `Cmd+K` / `Ctrl+K`. |
| **Dynamic "What's New in Update" Resolution** | Fixed updater dialog displaying generic download instructions instead of actual version changes. `antiagent.updater` now detects generic installer bodies and extracts true feature notes from `CHANGELOG.md` (both locally and via GitHub raw API). Enhanced CI release workflow to automatically embed release notes into GitHub releases. |

### Verification Proof
- All 300 unit tests passed (`python3 -m unittest discover tests`) with 0 failures and 0 errors.
- Added comprehensive unit test in `tests/test_dashboard.py` (`test_revamped_ui_structure`) verifying Telemetry HUD, View Tabs (1-7), Layout Toolbar, Card Focus & Collapse actions across all 9 cards, and Customization Modal.
- Added unit tests in `tests/test_updater.py` (`test_get_best_release_notes_replaces_installer_boilerplate`, `test_get_best_release_notes_keeps_custom_notes`) verifying release notes resolution.
- Validated complete HTML well-formedness with zero unclosed or mismatched tags using custom Python HTML parser.
- Validated JavaScript syntax and execution with Node.js (`node --check`).
- Verified 100% preservation of all 213 pre-existing element IDs and interactive APIs.

---

## [v0.4.0] — 2026-09-28

### Summary
Introduced **Codex-Style Prompt Queue, State-Aware Smart Enter, and Ctrl+S Steering for Antigravity / AGY CLI**. AntiAgent now acts as an intelligent PTY bridge and lifecycle state supervisor for `agy`, transforming the terminal interaction model into a high-productivity agent workflow similar to Codex and OpenCode. Features include non-destructive FIFO prompt queueing via Tab and Smart Enter, preview/approval boundary protection (preventing accidental confirmation of dialogs like `/teamwork-preview`), single-shot Ctrl+S agent steering with controlled interruption, safe lifecycle-driven dispatch contracts, atomic state persistence, and native fallback preservation.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Codex-Style FIFO Prompt Queue (`engine/prompt_queue.py`)** | Atomic, thread-safe, conversation-scoped FIFO prompt queue holding user instructions during active agent runs. Enforces generous limits (50 items, 100KB per prompt) and guarantees stale prompts from crashed/restarted sessions enter a held state rather than auto-replaying. |
| **State-Aware Smart Enter & Tab Queueing (`engine/pty_bridge.py`)** | Enter behavior adjusts dynamically based on agent lifecycle state and active surface: immediate submission when idle, safe FIFO queueing when active or waiting in an interactive modal. Tab key queues non-empty prompts while busy while preserving native shell autocomplete and focus when idle. |
| **Approval & Preview Protection** | Fixes critical boundary bug where pressing Enter while typing follow-up instructions accidentally accepted approval/preview screens (e.g. `/teamwork-preview`). Typed prompt text is safely intercepted and queued without propagating Enter to AGY; empty Enter preserves native modal navigation and approval. |
| **Ctrl+S Controlled Steering Coordinator** | Enables rapid agent redirection during active runs. Captures steering instructions, executes exactly one controlled interrupt keystroke without Esc-spamming, awaits safe lifecycle transition, and injects the steering message into the same conversation context. Preserves native Ctrl+S in settings and idle states. |
| **Antigravity Lifecycle State Integration (`flow_hook.py`)** | Dedicated lifecycle hook entrypoints (`pre-invocation`, `post-invocation`, `post-tool-use`, `stop`) maintaining canonical state in `InteractionStateStore` with atomic file persistence and temporary dispatch lease locking. `PreToolUse` verdicts (`ask`/`force_ask`) automatically flag approval boundaries. |
| **Centralized Interactive Surface Detection (`engine/interaction_state.py`)** | Real-time terminal output scanning fallback detecting teamwork previews, question dialogs, approval dialogs, and settings panels with regex matching. |
| **Reusable PTY Primitives (`engine/pty.py`)** | Extracted generic POSIX PTY process spawning, SIGWINCH terminal resizing, and guaranteed raw terminal restoration context managers shared between the interactive AGY wrapper and Remote Sessions. |
| **Interactive CLI Suite (`antiagent agy` & `antiagent queue`)** | Added `antiagent agy [AGY_ARGS...]` with transparent argument forwarding, recursion prevention, and automatic hook verification. Added `antiagent queue status` and `antiagent queue clear` subcommands. |

### Verification Proof
- All 297 unit tests passed (`python3 -m unittest discover tests`) with 0 failures and 0 errors.
- Added comprehensive test suites: `tests/test_interaction_state.py` (11 tests), `tests/test_prompt_queue.py` (24 tests), and `tests/test_pty_bridge.py` (28 tests).
- Verified mandatory regression test: `test_enter_with_typed_prompt_during_teamwork_preview_queues_without_approving`.
- Verified cross-process cache invalidation via nanosecond disk mtime tracking in `InteractionStateStore`.
- Verified multi-terminal queue synchronization and bounded terminal-item retention pruning in `PromptQueue`.
- Verified bracketed paste CRLF sanitization and escape sequence filtering in `PTYBridge`.
- Verified transparent `antiagent agy [AGY_ARGS...]` argument forwarding without `argparse` collisions.
- Verified rolling terminal observation buffer preventing chunk-boundary split misses.
- Verified non-destructive queue inspection (`antiagent queue status`) and session recovery (`antiagent queue resume`).

---

## [v0.3.0] — 2026-09-28

### Summary
Introduced the **Unified Antigravity Conversation Browser + Native `agy` Context Management Integration**. AntiAgent now discovers, indexes, inspects, and manages conversations across all Antigravity runtimes—Desktop, IDE, and CLI/`agy`. Rather than duplicating LLM context compression, AntiAgent deeply integrates with Antigravity's native compaction engine: detecting checkpoints, compactions, and resets while exposing rich context telemetry. Includes robust security isolation (blocking path traversal, symlink escapes, and credential leakage), strict chain-of-thought protection (never exposing internal thinking), high-performance bounded indexing with SQLite summary caching, full CLI commands, and a sleek dashboard interface.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Unified Multi-Source Discovery (`engine/conversations.py`)** | Centralized session discovery across Desktop (`~/.gemini/antigravity`), IDE (`~/.gemini/antigravity-ide`), and CLI (`~/.gemini/antigravity-cli`, `~/.gemini/agy`) using canonical `(source, conversation_id)` identity with environment variable isolation (`ANTIAGENT_DESKTOP_DIR`, `ANTIAGENT_IDE_DIR`, `ANTIAGENT_CLI_DIR`). |
| **Native Context & Compaction State Inspector** | Exposes native Antigravity auto-compaction and transcript lifecycle events (`CHECKPOINT`, `COMPACTION`, `CONTEXT_RESET`, `SESSION_RESET`). Computes turn counts, tool executions, transcript byte size, and extracts the latest compaction checkpoint summary directly from transcripts. |
| **Strict Security & Privacy Enforcement** | Rejects directory traversal (`../`, absolute paths) and verifies path containment preventing symlink escapes. Automatically redacts sensitive credentials in tool arguments (`password`, `token`, `secret`, `api_key`, `authorization`, Bearer tokens, GitHub PATs, OpenAI keys, PEM private keys). |
| **Chain-of-Thought (CoT) Protection** | Strips internal agent reasoning (`thinking`) across normalized message streams, dashboard UI, and Markdown/JSON transcript exports. Only user requests, assistant responses, and sanitized tool calls are rendered. |
| **High-Performance Bounded Indexing** | Optimized for massive histories using bounded head/tail byte inspections (<8KB head for first prompt and model, <16KB tail for latest prompt and compactions) combined with read-only SQLite `conversation_summaries.db` caching and in-memory mtime/size checks. |
| **Transcript Path Forwarding (`hook.py` & `context_extractor.py`)** | Hook payloads now capture and forward `transcriptPath`, enabling instant context resolution without redundant filesystem scans. |
| **CLI Conversation Management (`antiagent/cli.py`)** | Added full CLI suite: `antiagent conversations list` (with `--source`, `--search`, `--limit`, `--json`), `antiagent conversations show <id>`, `antiagent conversations export <id> [--format markdown|json]`, `antiagent conversations resume <id>`, and `antiagent remote conversations <machine> list`. |
| **REST API Suite (`antiagent/dashboard/server.py`)** | Added 6 secure endpoints: `GET /api/conversations`, `GET /api/conversations/detail`, `GET /api/conversations/context`, `POST /api/conversations/resume`, `POST /api/conversations/open`, and `POST /api/conversations/export`. |
| **Unified Dashboard UI (`index.html`)** | Added glassmorphic Conversations card with real-time source tabs (`All`, `Desktop`, `IDE`, `CLI`), 300ms debounced search, native context telemetry banner, conversation detail & context modal, transcript viewer, and resume/export actions. |
| **Doctor Diagnostics (`engine/doctor.py`)** | Enhanced `run_doctor_check` to report structured session breakdowns across Desktop, IDE, and CLI sources with direct jump actions in the dashboard. |

### Verification Proof
- All 215 unit tests passed (`python3 -m unittest discover tests`) with 0 failures and 0 errors.
- Added comprehensive test suite in `tests/test_conversations.py` (39 tests) covering multi-source discovery, SQLite summaries, path traversal/symlinks, redaction, CoT protection, API endpoints, and full CLI subcommands (`list`, `show`, `export`, `resume`, `remote conversations`).
- Verified live REST API endpoints and real Antigravity session discovery (156 sessions across Desktop & IDE).

---

## [v0.2.1] — 2026-09-28

### Summary
Resolved desktop app link delegation issues and refreshed the in-app update experience. Clicking GitHub release or repo links now reliably opens in the user's default external browser (Safari, Chrome, Arc) rather than being dropped by WebKit, and the release notes header now cleanly formats release titles and version tags. Also eliminated sticky stale success states in the 1-click in-place updater so users can re-sync or force an update at any time, and added local changelog fallbacks for rate-limited update checks.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Desktop WebKit Delegation (`main.swift`)** | Implemented `WKUIDelegate` (`createWebViewWith`) and `WKNavigationDelegate` (`decidePolicyFor`) in macOS native app to intercept external URL navigations and route them to `NSWorkspace.shared.open(url)`. |
| **Cross-Platform Open-URL API (`server.py`)** | Added `POST /api/open-url` endpoint validating `http`, `https`, and `mailto` schemes and calling `webbrowser.open(url)` as a universal fallback across desktop and web modes. |
| **Update Modal Polish (`index.html`)** | Removed forced uppercase styling on version labels, dynamically differentiated between pending updates and recent release notes, added direct GitHub Repository link alongside GitHub Release link, and added click-interception for all external links. |
| **1-Click Updater State Fix (`index.html`)** | Fixed issue where a previous successful update permanently hid the in-place update action button. The re-sync / force update button remains visible and accessible when running the latest version. |
| **Changelog Fallback (`updater.py`)** | Added `get_local_release_notes()` to automatically parse bundled `CHANGELOG.md` when GitHub releases API is offline or rate-limited. |

### Verification Proof
- All 171 unit tests passed (`python3 -m unittest discover tests`) with 0 failures and 0 errors.
- Verified `/api/open-url` endpoint, `get_local_release_notes()` extraction, and macOS Swift compilation.

---

## [v0.2.0] — 2026-09-28

### Summary
Introduced **Remote Sessions via SSH**, enabling developers and operators to seamlessly configure remote machines, probe Antigravity and AntiAgent runtime status, manage background `agy --remote-control` daemons, and launch interactive PTY sessions directly from AntiAgent. Built with zero external dependencies, strict standard OpenSSH client delegation, and robust security guarantees (zero password/key storage, no `StrictHostKeyChecking=no`, atomic config persistence, and comprehensive input validation).

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Native SSH Engine (`antiagent/engine/remote_sessions.py`)** | Implemented stdlib-only SSH engine utilizing system `ssh` and `scp` binaries. Features deterministic argument assembly (`build_ssh_argv`), OpenSSH batch mode execution, cached probing with configurable TTL, intelligent SSH error classification (auth failure, host verification failure, unreachable, timeout), and structured remote status parsing. |
| **Interactive Terminal Proxy (`launch_interactive_ssh`)** | Full pseudo-terminal (PTY) proxy with POSIX raw mode restoration, automatic terminal window resize synchronization (`SIGWINCH`), and real-time Antigravity web UI URL detection. Gracefully falls back to direct subprocess execution on Windows and non-TTY environments. |
| **Atomic Host Registry (`~/.antiagent/remotes.json`)** | Secure, multi-host configuration store with atomic write-replace (`tempfile.NamedTemporaryFile` + `os.replace`), strict POSIX file permissions (`0600`), and robust input validation preventing argument and command injection. |
| **Comprehensive CLI Suite (`antiagent remote`)** | Added 12 complete subcommands: `list`, `add`, `remove`, `show`, `test`, `doctor`, `login`, `status`, `start`, `stop`, `connect`, and `protect` with rich tabular terminal formatting and actionable diagnostic advice. |
| **Dashboard Integration & REST Endpoints** | Added 9 secure REST endpoints under `/api/remotes/*` (`GET /api/remotes`, `GET /api/remotes/status`, `GET /api/remotes/doctor`, `POST /api/remotes/add`, `POST /api/remotes/remove`, `POST /api/remotes/test`, `POST /api/remotes/start`, `POST /api/remotes/stop`, `POST /api/remotes/protect`) protected by origin verification and host header checks. |
| **Modern Dashboard UI (`index.html`)** | Added dark glassmorphic Remote Sessions management card, real-time status badges, machine addition/editing modal with connection testing, diagnostics doctor modal, and one-click interactive connect instructions. |
| **Automated Verification Suite** | Added 43 comprehensive unit tests across `tests/test_remote_sessions.py`, `tests/test_cli.py`, and `tests/test_dashboard.py`, verifying validation, injection prevention, atomic file operations, CLI execution, error classification, and API handlers. |

### Verification Proof
- All 169 unit tests passed (`python3 -m unittest discover tests`) in ~2.5s with 0 failures and 0 errors.
- Verified CLI commands (`list`, `add`, `test`, `doctor`, `connect`, etc.) and dashboard endpoints end-to-end.

---

## [v0.1.9] — 2026-09-22

### Summary
Enhanced the self-updater experience with rich markdown release notes rendering, unified desktop app relaunch and process restarts, resilient PEP 668 pip fallbacks, and intelligent pending-restart detection. Users now see formatted release changelogs with tables and links directly inside the dashboard update modal, and a single, intelligent restart action that safely relaunches the desktop application or restarts the CLI server while polling until the new version is live.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Rich Markdown Release Notes** | Implemented client-side `renderMarkdown` in `antiagent/dashboard/assets/index.html` with full support for tables (`changelog-table-wrap`), blockquotes, code blocks, task lists, and safe autolinking, transforming raw GitHub release markdown into readable, themed documentation with an external GitHub release link. |
| **Unified Desktop & Server Relaunch** | Replaced fragmented relaunch/restart controls with unified restart routing in `antiagent/dashboard/server.py`. Added `find_running_or_installed_desktop_app()` and `_relaunch_desktop_worker()` to gracefully quit and relaunch `AntiAgent.app` on macOS, with client-side polling on `/api/status` until the updated version responds before reloading. |
| **PEP 668 Pip Fallback Resilience** | Added automatic multi-tier retry in `InPlaceSelfUpdater` (`antiagent/updater.py`) for Python package upgrades in externally managed environments, attempting standard pip, `--break-system-packages`, and `--user` install modes. |
| **Pending Restart State Machine** | Added `restart_pending` and `desktop_app_running` flags to `InPlaceSelfUpdater.get_status()`, cleanly distinguishing between files installed on disk and active running versions with clear "v{version} Installed — Restart Required" badges. |
| **Automated Verification** | Added unit tests in `tests/test_dashboard.py` and `tests/test_updater.py` covering restart endpoint responses, release notes rendering assets, and pending-restart state evaluation. |

### Verification Proof
- All 127 unit tests passed (`python3 -m unittest discover tests`) in ~2.5s.
- Verified desktop app detection, relaunch worker script generation, release notes markdown parsing, and restart polling behavior.

---

## [v0.1.8] — 2026-09-22

### Summary
Overhauled the 1-Click In-Place Self-Updater to eliminate confusing UI states, stuck update modals, and pip packaging errors during self-updates. In v0.1.7, an earlier successful update cached status in memory and permanently rendered a stale success banner while hiding the action button when a newer release became available. v0.1.8 adds semantic version comparison, automatic state reset, robust GitHub source-tarball fallback for pip upgrades, granular multi-component progress indicators, an expandable live log view, and error recovery with retry.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Stale State Recovery** | Added `compareVersions()` in client-side JavaScript and automatic reset via `/api/update/self_update_reset` when a new GitHub release is detected, preventing older success or error states from masking new updates. |
| **Source Tarball Pip Updates** | Fixed `pip install` errors on macOS where pip was invoked on `AntiAgent.zip` (which is a macOS `.app` bundle, not a Python package). Updater now fetches the release source tarball (`v{version}.tar.gz`) for pip installs while using `ditto` to update the native `/Applications/AntiAgent.app` bundle. |
| **Multi-Component Tracking** | Enhanced `InPlaceSelfUpdater` and dashboard modal to track individual component statuses (`macOS Desktop App` and `Python Package`) independently with live badge indicators. |
| **Live Diagnostics & Error UI** | Added a collapsible "View Logs" drawer for real-time update diagnostics, a dedicated error card with failure reason, retry action, and an explicit "✕ Dismiss" option. |
| **App Relaunch & Test Safety** | Added `/api/update/relaunch_app` endpoint to trigger macOS app reopening, and hardened `/api/update/restart` with synchronous test guards to eliminate subprocess test interference. |
| **Automated Verification** | Added comprehensive unit tests in `tests/test_updater.py` and `tests/test_dashboard.py` covering state resets, component status reporting, endpoint handling, and process restarts. |

### Verification Proof
- All 125 unit tests passed (`python3 -m unittest discover tests`) in ~2.2s.
- Verified updater UI transitions across checking, downloading, extracting, applying, success, and error states.

---

## [v0.1.7] — 2026-09-22

### Summary
Introduced a high-density, real-time Activity Sidebar and audit log toggle (`audit_include_tool_calls`), solving the problem of agent tool calls, file accesses, searches, and exploratory operations getting buried in chat accordions during complex tasks. Developers and security auditors can now view, filter, dock, and inspect every command and tool invocation with full JSON payloads and rationale without cluttering default command logs.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Activity Sidebar** | Added slide-out, dockable/pinnable Activity Sidebar (`#activitySidebar`) in `antiagent/dashboard/assets/index.html` with keyboard shortcut (<kbd>Cmd</kbd>+<kbd>B</kbd> / <kbd>Ctrl</kbd>+<kbd>B</kbd>), live activity count badge, category filters (Commands, Files, Searches, Other), verdict chips, and real-time search filtering. |
| **Activity Inspector Modal** | Added dense JSON inspector modal (`#activityDetailModal`) showing timestamp, tool name, verdict badge, formatted JSON arguments, agent rationale, and conversation context with 1-click clipboard copy. |
| **Tool Calls Audit Toggle** | Added `audit_include_tool_calls: bool` config parameter (default `False`), configurable via dashboard switch in Card 3, CLI (`antiagent config --set-audit-include-tool-calls true|false`), or environment variable `ANTIAGENT_AUDIT_INCLUDE_TOOL_CALLS`. |
| **Hook & Audit Logging** | Updated `antiagent/hook.py` and `antiagent/audit/logger.py` to record tool calls and explorations when enabled, while maintaining lean command-only audit records by default. |
| **API & CLI Enhancements** | Added `include_tool_calls` query parameter to `GET /api/audit`, exposed status in `GET /api/status`, added `--all-tools` and `--commands-only` flags to `antiagent audit`, and displayed state in `antiagent status`. |
| **Automated Verification** | Added comprehensive unit test suite in `tests/test_activity_sidebar.py` verifying config persistence, hook logging gating, audit log filtering, API endpoints, CLI arguments, and DOM elements. |

### Verification Proof
- All 124 unit tests passed (`python3 -m unittest discover tests`).
- Verified live rendering, docking, search, filtering, and detail modal inspection across all tool categories and verdicts.

---

## [v0.1.6] — 2026-09-22

### Summary
Introduced a seamless 1-Click In-Place Self-Updater (`InPlaceSelfUpdater`), eliminating manual DMG mounting, PKG installer prompts, and app bundle reinstallations. Enables background download streaming, in-place app bundle patching via `ditto`, pip environment synchronization, clean in-place process restart, and a redesigned update modal. Also resolves macOS Gatekeeper launch errors and archive permission degradation after self-updating.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **In-Place Self-Updater** | Built `InPlaceSelfUpdater` engine (`antiagent/updater.py`) with thread-safe stage progression (`idle` -> `checking` -> `downloading` -> `extracting` -> `applying` -> `success` / `error`), download speed and percentage metrics, and cancellation support. |
| **Zero-Reinstall macOS Patching** | Streamlines macOS bundle updates (`/Applications/AntiAgent.app` and `~/Applications/AntiAgent.app`) using native `ditto` synchronization, avoiding "file in use / open app" trash errors. |
| **Permissions & Gatekeeper Fixes** | Replaced standard `zipfile.extractall()` with native `ditto -x -k` and POSIX mode restoration fallback (`external_attr >> 16`), ensuring Mach-O binaries retain `+x` (`0o755`) executable permissions. Added automatic quarantine removal (`xattr -cr`), ad-hoc bundle re-signing, and LaunchServices registration refresh (`lsregister -f`). |
| **Prevent Bundle Sealing Violations** | Added `PYTHONDONTWRITEBYTECODE=1` and `-B` to Python backend spawn arguments in `main.swift`, preventing unsealed `.pyc` bytecode caching inside `.app/Contents/Resources`. |
| **Dashboard API Endpoints** | Added `POST /api/update/self_update`, `GET /api/update/self_update_status`, `POST /api/update/self_update_cancel`, and `POST /api/update/restart` in `antiagent/dashboard/server.py`. |
| **Redesigned Update Modal** | Front-and-center 1-Click Update card with live progress bar, speed indicator, step narrative, and 1-click dashboard reload; manual DMG/PKG/ZIP downloads cleanly collapsed. |
| **Process Replacement** | Added instant in-place restart via `os.execv` preserving port and active workspace context. |

### Verification Proof
- All 115 unit tests passed (`python3 -m unittest discover tests`).
- Verified self-updater state machine transitions, cancellation handling, archive permission preservation, and live application launch on macOS.

---

## [v0.1.5] — 2026-09-22

### Summary
Elevated the AntiAgent dashboard into an elite black-and-white luxury aesthetic inspired by Y Combinator and Luma, featuring an interactive constellation canvas, double-bezel smoked glass cards, Shadcn-grade sheen buttons, liquid glass toggle switches, and Apple-grade fluid sliding tabs.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **Dashboard Aesthetics** | Pure OLED pitch-black theme (`#050507`) with double-bezel smoked glass cards (`backdrop-filter: blur(24px)`), hairline borders, tactile noise texture, and cursor spotlight. |
| **Canvas Graphics** | High-performance HTML5 interactive constellation canvas with organic particle physics, mouse repulsion, and spring damping. |
| **Iconography & Typography** | Replaced all emojis with bespoke 1.5px SVG vector icons; standardized on `Geist` and `JetBrains Mono` typography. |
| **Micro-Interactions** | Shadcn-style primary button sheen sweep and active depression; liquid glass toggles with dynamic fluid thumb stretching. |
| **Segmented Controls** | Added Apple-grade sliding indicator pill (`#stanceIndicator`) with organic spring physics (`cubic-bezier(0.34, 1.28, 0.64, 1)`), sub-pixel alignment, and instant tactile feedback. |
| **Security & CSP** | Updated Content Security Policy in `antiagent/dashboard/server.py` to allow Google Fonts securely while preserving strict local isolation. |

### Verification Proof
- All 111 unit tests passed (`python3 -m unittest discover tests`).
- Pixel precision and motion responsiveness verified in Chromium headless and live browser on port 4242.

---

## [v0.1.4] — 2026-09-22

### Summary
Introduced Claude Code-style Auto-PR monitoring, live GitHub Actions CI watcher, failure log retrieval, auto-remediation workflows, and auto-merge arming.

---

## [v0.1.3] — 2026-09-21

### Summary
Added cross-platform Windows support with UTF-8 console protections and overhauled the onboarding experience with Antigravity Turbo Mode setup guide.

---

## [v0.1.2] — 2026-09-20

### Summary
Added auto-opening macOS PKG installer, portable DMG, and standalone ZIP distributions.

---

## [v0.1.1] — 2026-09-19

### Summary
Introduced interactive project directory switching and setup health diagnostics.

---

## [v0.1.0] — 2026-09-18

### Summary
Initial release of AntiAgent intelligent safety supervisor and pre-tool-use gatekeeper for Google Antigravity.
