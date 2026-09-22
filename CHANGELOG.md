# Changelog

All notable changes to the **AntiAgent** project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v0.1.6] — 2026-09-22

### Summary
Introduced a seamless 1-Click In-Place Self-Updater (`InPlaceSelfUpdater`), eliminating manual DMG mounting, PKG installer prompts, and app bundle reinstallations. Enables background download streaming, in-place app bundle patching via `ditto`, pip environment synchronization, clean in-place process restart, and a redesigned update modal.

### Architectural & Functional Highlights

| Area / Component | Improvement |
| :--- | :--- |
| **In-Place Self-Updater** | Built `InPlaceSelfUpdater` engine (`antiagent/updater.py`) with thread-safe stage progression (`idle` -> `checking` -> `downloading` -> `extracting` -> `applying` -> `success` / `error`), download speed and percentage metrics, and cancellation support. |
| **Zero-Reinstall macOS Patching** | Streamlines macOS bundle updates (`/Applications/AntiAgent.app` and `~/Applications/AntiAgent.app`) using native `ditto` synchronization, avoiding "file in use / open app" trash errors. |
| **Dashboard API Endpoints** | Added `POST /api/update/self_update`, `GET /api/update/self_update_status`, `POST /api/update/self_update_cancel`, and `POST /api/update/restart` in `antiagent/dashboard/server.py`. |
| **Redesigned Update Modal** | Front-and-center 1-Click Update card with live progress bar, speed indicator, step narrative, and 1-click dashboard reload; manual DMG/PKG/ZIP downloads cleanly collapsed. |
| **Process Replacement** | Added instant in-place restart via `os.execv` preserving port and active workspace context. |

### Verification Proof
- All 114 unit tests passed (`python3 -m unittest discover tests`).
- Verified self-updater state machine transitions, cancellation handling, and already-up-to-date detection.

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
