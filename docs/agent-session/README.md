# AI Agent Session Log & Decisions

This directory contains the exported AI-agent session record for the **Weather Risk Intelligence Agent (WRIA)** project, fulfilling the mandatory session-log deliverable.

## Contents

- [`AGENTS.md`](../../AGENTS.md) — The living master plan, architecture specification, MySQL schema, WDRI v1 scoring formulas, milestone definitions, and progress log (`§11`).
- [`session-transcripts.md`](session-transcripts.md) — Chronological export of all 21 AI-agent sessions (user prompts, step counts, tool call counts, and final responses, with any secret tokens redacted) spanning planning, M0–M10 implementation, testing, security review, UI refinement, voice I/O, and deployment.

## Chronological Session Summary

1. **Planning (`7e53ed4c`):** Analyzed `ASSIGNMENT.pdf` and authored `AGENTS.md` defining the 20-hub portfolio, MySQL 8.0 6-table schema, deterministic WDRI v1 scoring engine, JSON-step agent loop, SSE streaming, evaluation suite, and M0–M10 milestones.
2. **M0–M2 (`cbd84e7b`, `464e4b36`, `9eedb9a2`, `82474e9a`):** Scaffolded FastAPI + Vite React + Docker Compose, implemented Alembic `0001_initial` + 20-hub seeding + FEMA NRI v1.20 extraction (`nri_hubs.json`), and built the Open-Meteo 5-year backfill & NWS active-alerts client.
3. **M3–M5 (`c3607547`, `83c24fd3`):** Implemented and calibrated the pure-Python WDRI v1 scoring engine, REST read endpoints, OpenRouter JSON-step agent loop (`nvidia/nemotron-3.5-super-vl:free` + fallbacks), 6 deterministic tools, grounding validator, deterministic fallback, SSE chat streaming (`POST /api/chat/stream`), and per-IP sliding-window rate limiter.
4. **M6 & Full-Stack E2E (`0b1ea853`, `a7bc121d`):** Built the 3-column React UI (`HubSidebar`, `ChatWindow`, `AlertsPanel`, `ToolTrace`, `MethodologyModal`, deterministic artifact cards) and added a Playwright full-stack integration test.
5. **M7 & Multi-Model Selector (`f392b6e0`, `0a11037b`):** Added APScheduler periodic refresh, snapshot-diff `risk_alerts` detector, `/api/alerts/stream` SSE broadcaster, outgoing webhook notifier, `/api/admin/*` token-protected triggers (`rescore`, `simulate-alert`), and a UI/API model selector (`0002_add_session_model.py`).
6. **M8 Evaluations (`3441cb98`):** Built `backend/evals/` (15 cases, live DB oracles, graders, runner) and ran against OpenRouter free models, identifying and fixing 4 issues (tool-arg merging in `run.py`, grounding false positives on month-day dates and formula constants, Windows `cp1252` console encoding, and prompt-injection refusal phrasing).
7. **Security Audit, Voice I/O, UI Simplification & Retrospective (`60b1d350`, `b182dc8d`, `3b37b45e`, `f3f26e6e`):** Audited and hardened security controls, added browser-native Web Speech API voice input/output with 16 Vitest tests, simplified the UI layout, and distilled project lessons into reusable skills.
8. **M9 Deployment (`8c5c2c85`):** Configured Railway (MySQL + backend + frontend) and Vercel (`vercel.json`), added `mysql://` → `mysql+asyncmy://` URL normalization, backfilled & rescored production MySQL, and verified live SSE + Q1–Q4 responses.
9. **M10 Documentation & Deliverables (`4200a7d3`):** Updated `README.md`, authored `docs/ARCHITECTURE.md`, exported `docs/agent-session/`, aligned the Playwright E2E test with the simplified UI, and verified all backend/frontend tests and builds.
