# Changelog

All notable changes to the **AntiAgent** project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
