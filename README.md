# Weather Risk Intelligence Agent (WRIA)

AI-powered weather disruption risk analyst for a 20-hub US logistics distribution portfolio. WRIA combines a deterministic **Weather Disruption Risk Index (WDRI v1)** scoring engine (Open-Meteo 5-year historical reanalysis + county-level FEMA National Risk Index + live NWS alerts) with an OpenRouter-powered JSON-step tool agent, streaming SSE chat UI, Web Speech API voice input/output, and real-time risk-change alerting.

## Live Deployment

- **Frontend UI (Railway):** https://moveo-home-ass-ist.up.railway.app
- **Backend API (Railway):** https://backend-production-20146418.up.railway.app
- **Health Check:** `GET https://backend-production-20146418.up.railway.app/api/health`
- *(Vercel SPA rewrites are also pre-configured in `frontend/vercel.json` if deploying the frontend to Vercel.)*

---

## Prerequisites

- **Docker & Docker Compose** (for local MySQL 8.0 or full-stack container run)
- **Python 3.11+** (tested on 3.11)
- **Node.js 18+** (tested on Node 18)
- **OpenRouter API Key** (free tier works with `:free` models)

---

## Quick Start (Docker Compose)

1. Copy `.env.example` to `.env` and set `OPENROUTER_API_KEY` (and optionally `ADMIN_TOKEN` if you want to trigger admin endpoints):
   ```bash
   cp .env.example .env
   ```
2. Start MySQL, backend, and frontend:
   ```bash
   docker compose up --build
   ```
3. Run initial migrations, seed the 20 hubs + FEMA NRI scores, backfill 5 years of Open-Meteo weather data, and compute the initial risk snapshots (in a second terminal):
   ```bash
   docker compose exec backend alembic upgrade head
   docker compose exec backend python -m scripts.seed --if-empty
   docker compose exec backend python -m scripts.backfill_weather
   docker compose exec backend python -m scripts.rescore
   ```
4. Verify:
   - Backend health: `curl http://localhost:8000/api/health` → `{"status":"ok","db":"ok",...}`
   - Frontend UI: open `http://localhost:5173`

---

## Local Development (Manual)

### 1. Database (MySQL 8.0)

Start only MySQL via Docker Compose (or point `DATABASE_URL` in `.env` to your own MySQL 8.0 instance):
```bash
docker compose up -d mysql
```

### 2. Backend (FastAPI)

```bash
cd backend
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# Unix/macOS:         source .venv/bin/activate
pip install -e ".[dev]"

# Apply schema migrations & seed 20 hubs + FEMA NRI v1.20
alembic upgrade head
python -m scripts.seed --if-empty

# Backfill 5 full calendar years + YTD from Open-Meteo and compute WDRI snapshots
python -m scripts.backfill_weather
python -m scripts.rescore

# Start API server
uvicorn app.main:app --reload --port 8000
```

> **Note:** `backend/app/data/nri_hubs.json` is committed in the repo. To re-extract FEMA NRI county scores from scratch, run `python -m scripts.fetch_nri`. To discover currently available free OpenRouter models with `response_format` support, run `python -m scripts.list_free_models`.

### 3. Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173`.

---

## Environment Variables

All variables are documented in `.env.example`:

| Variable | Default / Example | Description |
|---|---|---|
| `DATABASE_URL` | `mysql+asyncmy://wria:wria@localhost:3306/wria` | Async MySQL 8.0 connection string (`mysql://` and `mysql+pymysql://` are auto-normalized to `mysql+asyncmy://`) |
| `OPENROUTER_API_KEY` | `sk-or-...` | OpenRouter API key (required for live LLM responses; falls back deterministically if unset/rate-limited) |
| `OPENROUTER_MODEL` | `nvidia/nemotron-3.5-super-vl:free` | Primary OpenRouter model ID |
| `OPENROUTER_FALLBACK_MODELS` | `google/gemma-4-31b-it:free,baidu/ernie-5.5-flash-preview:free` | Comma-separated fallback models |
| `OPENROUTER_APP_URL` / `OPENROUTER_APP_NAME` | `http://localhost:5173` / `Weather Risk Intelligence Agent` | OpenRouter attribution headers |
| `NWS_USER_AGENT` | `WeatherRiskAgent (you@example.com)` | Required `User-Agent` header for `api.weather.gov` |
| `ADMIN_TOKEN` | *(random secret)* | Required in `X-Admin-Token` header for `POST /api/admin/rescore` and `POST /api/admin/simulate-alert` |
| `ALERT_WEBHOOK_URL` | *(optional URL)* | Outgoing webhook target (e.g., Slack or `webhook.site`) for risk alerts |
| `ALERT_SCORE_DELTA` | `10` | Minimum `|Δ operational_score|` to trigger a `score_delta` alert |
| `SCHEDULER_ENABLED` | `true` | Enables APScheduler (NWS alerts every 30m, weather daily at 06:00 UTC) |
| `NWS_REFRESH_MINUTES` / `WEATHER_REFRESH_CRON` | `30` / `0 6 * * *` | Background refresh intervals |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowed frontend origins |
| `TRUST_PROXY_HEADERS` | `false` | Trust `X-Forwarded-For` for per-IP rate limiting behind a reverse proxy |
| `CHAT_RATE_LIMIT_PER_MIN` | `10` | Per-IP rate limit on `/api/chat` and `/api/chat/stream` |
| `MAX_AGENT_STEPS` | `6` | Maximum tool/reasoning steps per turn |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Backend base URL used by the Vite frontend |

---

## Running Tests & Evaluations

### Backend Tests (`pytest`)
Includes unit tests for WDRI scoring, alert detection, schemas, `respx`-mocked ingestion, SSE streaming, FastAPI routes, and a Playwright full-stack browser integration test (uses in-memory/temp SQLite automatically):
```bash
cd backend
pytest -q
```

### Frontend Tests & Build (`vitest` + `vite`)
```bash
cd frontend
npm test
npm run build
```

### Agent Evaluation Suite (`evals`)
Runs 15 cases (the 4 assignment questions, 3 follow-ups, 2 out-of-scope, 1 prompt injection, 2 operational, 2 statistic variants, 1 ambiguous) against live DB oracles:
```bash
cd backend
python -m evals.run --delay 3
```
Latest committed report: `backend/evals/results/latest.md`.

---

## Demo Script

1. **Open the UI** (`http://localhost:5173` or the Railway URL). Confirm the right-hand **Alerts & Status** panel shows `API Online`, `DB OK`, and `● Live`.
2. **Run the 4 core questions** (clickable starter chips or typed/spoken via the microphone button):
   - *Q1 (Filtered ranking):* `"Which hubs in the Midwest are most exposed to winter disruption?"` → Inspect the **How I got this** tool trace (`rank_hubs`) and the deterministic `RankingTable` artifact (MSP, ORD, DTW).
   - *Q2 (Multi-hub comparison):* `"Compare Miami and Houston in terms of hurricane and flood exposure."` → Inspect `ComparisonTable` (MIA vs IAH).
   - *Q3 (Weather statistic):* `"What percentage of days in Denver last year had snowfall?"` → Inspect `StatCard` from `get_weather_stats`.
   - *Q4 (Score attribution):* `"Why is the Dallas hub's weather disruption risk high?"` → Inspect `ScoreBreakdown` + `AssumptionsBox`.
3. **Test conversational follow-up:** Ask `"What about Chicago specifically, why?"` in the same session to verify session memory.
4. **Inspect Methodology:** Click **Methodology** in the header to view live weights, caps, and thresholds served from `GET /api/meta/scoring`.
5. **Trigger a live alert (`simulate-alert`):**
   ```bash
   curl -X POST http://localhost:8000/api/admin/simulate-alert \
     -H "Content-Type: application/json" \
     -H "X-Admin-Token: $ADMIN_TOKEN" \
     -d '{"hub":"MIA","severity":"Extreme","event":"Hurricane Warning"}'
   ```
   A `new_severe_weather` / `tier_change` alert is persisted to `risk_alerts`, pushed over `GET /api/alerts/stream` into the UI toast + **Alerts & Status** feed within 2 seconds, and POSTed to `ALERT_WEBHOOK_URL` if configured.

---

## Documentation

- **System Architecture & Storage Design:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **Build Plan & Specification:** [`AGENTS.md`](AGENTS.md)
- **AI Agent Session Logs (Deliverable):** [`docs/agent-session/`](docs/agent-session/)
