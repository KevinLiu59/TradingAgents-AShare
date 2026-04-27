# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TradingAgents-AShare is a multi-agent LLM financial trading system for A-share (Chinese stock market) analysis. It simulates a top-tier investment research institution with 14 agents performing multi-round bull/bear debates and risk control博弈 to produce structured trading recommendations.

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, LangGraph (multi-agent orchestration), SQLAlchemy, SQLite/Redis
- **Frontend**: React 18 + TypeScript, Vite, TailwindCSS v4, Zustand (state management), Recharts, lightweight-charts
- **Data**: akshare, baostock, yfinance, stockstats for A-share market data
- **LLM**: Multi-provider (OpenAI, Anthropic, Google Gemini, DeepSeek, Moonshot, 智谱, etc.)

## Key Commands

### Backend

```bash
# Install dependencies
uv sync

# Start API server (serves both API + static frontend)
uv run python -m uvicorn api.main:app --port 8000
# or via entry point:
uv run tradingagents-api

# Run tests
uv run pytest
uv run pytest tests/test_intent_parser.py  # single test file
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # dev server with HMR
npm run build      # production build (outputs to dist/)
npm run lint       # ESLint
npm run preview    # preview production build
```

### Scheduler (scheduled analysis tasks)

```bash
uv run tradingagents-scheduler
# or:
python -m scheduler.main
```

### Docker

```bash
docker build -t tradingagents-ashare .
docker run -d -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -e DATABASE_URL="sqlite:///./data/tradingagents.db" \
  -e TA_APP_SECRET_KEY="$(openssl rand -base64 32)" \
  tradingagents-ashare
```

## Architecture

### Backend (`api/`)

FastAPI application serving REST API + static frontend files. Key modules:

| Module | Purpose |
|--------|---------|
| `api/main.py` | FastAPI app entry point, route registration, static file serving |
| `api/database.py` | SQLAlchemy models & DB operations (reports, users, portfolios, scheduled tasks) |
| `api/job_store*.py` | Job queue management (SQLite + Redis backends) |
| `api/services/` | Business logic: auth, reports, portfolio import, scheduled analysis, tracking board, VLM parsing, WeCom notifications |

### Core Trading Engine (`tradingagents/`)

LangGraph-based multi-agent system with 14 agents organized into departments:

```
tradingagents/
  graph/
    trading_graph.py       # Main orchestrator - TradingAgentsGraph class
    conditional_logic.py   # Debate termination conditions
    data_collector.py      # One-shot data pre-fetching
    intent_parser.py       # Natural language query parsing
    propagation.py         # State initialization & graph execution
    reflection.py          # Post-trade reflection
    signal_processing.py   # Output formatting
  agents/
    analysts/              # 6 analysts: market, social, news, fundamentals, macro, smart_money, volume_price
    researchers/           # Bull & bear researchers for debate
    managers/              # Research manager (arbiter), Risk manager
    risk_mgmt/             # Aggressive, conservative, neutral risk debators
  dataflows/               # Data fetching: akshare, baostock, yfinance, stockstats
  llm_clients/             # Multi-provider LLM abstraction (OpenAI, Anthropic, Google)
  prompts/                 # Bilingual prompt catalogs (zh/en)
```

**Agent workflow**: Intent parsing → Data collection → Analyst reports → Bull/Bear debate → Research judge → Trader plan → Risk debate → Portfolio manager final decision

### Frontend (`frontend/src/`)

React SPA with React Router, organized by pages/components/stores:

| Directory | Purpose |
|-----------|---------|
| `pages/` | Route-level components: Analysis, Dashboard, Reports, Portfolio, Settings, etc. |
| `components/` | Reusable UI: DebateDrawer, DebateTimeline, DecisionCard, KlinePanel, etc. |
| `stores/` | Zustand state stores (analysisStore, authStore) |
| `hooks/` | Custom hooks: useSSE (server-sent events), useTypeWriter |
| `services/api.ts` | API client |

### Scheduler (`scheduler/`)

Standalone async process that polls for scheduled analysis tasks, executes them with concurrency control (Semaphore), and handles failure retry/disabling.

## Environment Variables

See `.env.example` for all options. Key ones:

- `TA_API_KEY` / `TA_BASE_URL` / `TA_LLM_PROVIDER` — Core LLM configuration
- `TA_APP_SECRET_KEY` — Encrypts API keys & signs JWT tokens (required for production)
- `DATABASE_URL` — SQLite path (default: `sqlite:///./data/tradingagents.db`)
- `TA_VLM_API_KEY` — Vision model for position screenshot parsing
- `MAIL_*` — SMTP for email verification login

LLM API keys for end users are configured in the frontend Settings page and encrypted at rest.

## Important Notes

- Python entry points defined in `pyproject.toml`: `tradingagents`, `tradingagents-api`, `tradingagents-scheduler`
- The API server also serves the built frontend static files
- LangGraph uses a shared `MemorySaver` checkpointer (singleton) for concurrency
- Debates are claim-driven with structured rounds (configurable via `TA_MAX_DEBATE` / `TA_MAX_RISK`)
- The system supports dual-horizon analysis (short-term and medium-term)
- Scheduled tasks auto-reuse trading context and auto-disable after consecutive failures
