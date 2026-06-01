# ofin

Self-hosted dashboard for m's Brazilian financial life, backed by
[Pluggy](https://pluggy.ai). Pulls accounts / transactions / credit-card
bills / investments from Brazilian banks via Pluggy's aggregator API,
caches them in Postgres, renders a server-side dashboard with PT-BR
categorization, monthly cashflow, top merchants, and PIX in/out.

Lives at https://ofin.mvr.ac (Authelia-gated).

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.x async + asyncpg
- **DB**: PostgreSQL 16
- **Frontend**: Jinja2 templates + Pluggy Connect widget (CDN JS)
- **Sync**: APScheduler (12h) + webhook receiver (`/webhook/pluggy`)
- **Container**: `python:3.12-slim`

## Layout

```
src/ofin/
  main.py          FastAPI app, lifespan
  config.py        pydantic-settings (env vars)
  db.py            async engine + session factory
  models.py        Item / Account / Transaction / Investment / Webhook
  pluggy.py        REST client (auth cache, cursor pagination)
  sync.py          upsert + per-item / full sync
  scheduler.py     APScheduler 12h job
  analyzer.py      cashflow / by_category / by_merchant / pix_volumes
  routes/pages.py  Jinja-rendered dashboard / items / transactions
  routes/api.py    JSON: connect-token, items CRUD, force-sync
  routes/webhook.py POST /webhook/pluggy (HMAC verify)
  templates/       base, dashboard, connect, items, transactions
  static/styles.css
```

## Run locally

```bash
cp .env.example .env
# fill PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET from dashboard.pluggy.ai
docker run -d --name ofin-db -e POSTGRES_USER=ofin -e POSTGRES_PASSWORD=ofin \
  -e POSTGRES_DB=ofin -p 5432:5432 postgres:16-alpine
pip install -e .
DATABASE_URL=postgresql+asyncpg://ofin:ofin@localhost:5432/ofin \
  uvicorn ofin.main:app --port 8080 --reload
```

Open http://localhost:8080/connect, click "open pluggy connect", pick the
Sandbox connector, complete with `user-ok` / `password-ok` / MFA `123456`.
The item gets registered, synced, and visible on `/`.

## Deploy

See `compose/ofin/README.md` in the nixos repo.

## Pluggy notes

- Auth: `POST /auth {clientId, clientSecret}` → API key valid 2h.
- Connect token: short-lived (30 min), backend → frontend, embedded into
  the hosted widget. `onSuccess` returns `item.id` (UUID).
- Transactions endpoint is `/v2/transactions` (cursor-paged, `pageSize`
  up to 500, `dateFrom` filter, ~12 months retention default).
- Webhooks events of interest: `item/created`, `item/updated`,
  `transactions/created`, `transactions/updated`, `item/error`.
- `meu.pluggy.ai` Data Passport — single-user free path, proxies your own
  items into the Dashboard's clientId/secret without per-item billing.
