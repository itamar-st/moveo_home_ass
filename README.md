# Weather Risk Intelligence Agent

AI-powered weather disruption risk analyst for a US logistics distribution hub portfolio.

## Prerequisites

- Docker & Docker Compose (for local MySQL + full-stack run)
- Python 3.11+ (for local backend development)
- Node.js 18+ (for local frontend development)

## Quick Start (Docker Compose)

1. Copy `.env.example` to `.env` and fill in `OPENROUTER_API_KEY`.
2. Start all services:
   ```bash
   docker compose up --build
   ```
3. Verify:
   - Backend health: `curl http://localhost:8000/api/health`
   - Frontend UI: open `http://localhost:5173`

## Local Development (Manual)

### Backend
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate  |  Unix: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Run tests:
```bash
cd backend
pytest
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Build check:
```bash
cd frontend
npm run build
```

## Documentation

- Architecture & design: `docs/ARCHITECTURE.md` (coming in M10)
- Build plan & spec: `AGENTS.md`
