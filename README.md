# Supply Room — Check-Out Station

Touchscreen kiosk for tool and station-part checkouts. **PostgreSQL is the source of truth**; the kiosk is a thin UI; Google Sheets / Excel is a **purchasing mirror**.

![CI](https://github.com/ghorbelhoucem/supply-room-kiosk/actions/workflows/ci.yml/badge.svg)

---

## Stack

| Layer | Technology |
|---|---|
| UI | Vanilla HTML/CSS/JS (`index.html` + `src/`) |
| API | Python FastAPI (`backend/`) |
| Database | PostgreSQL |
| Buyer view | Google Sheets mirror + `.xlsx` export |
| Hosting | Docker Compose (nginx kiosk + api + db) |

---

## Quick start (Docker / company LAN)

```bash
git clone https://github.com/ghorbelhoucem/supply-room-kiosk.git
cd supply-room-kiosk
git checkout feature/production-inventory-backend

# Optional: set JWT_SECRET in .env
docker compose up -d --build
```

Open:

```text
http://10.3.120.174:61938
```

Seeded demo PINs (change in production via DB / reseed):

| Role | PIN | User |
|---|---|---|
| Maintenance | `4708` | Houcem |
| Management | `4685` | Rosa |
| Devs | `7346` | Developer |

Demo operators: ID `1` / `Senate!now1` (Supervisor), ID `2` / `Punch+love2` (Tele-operator).

---

## Architecture

- Kiosk calls `/api/*` through nginx → FastAPI
- Auth: `POST /api/auth/login/pin` or `/api/auth/login/operator` → JWT
- Mutations: `POST /api/take-batch`, `/api/return-batch`, `/api/receive`, `/api/adjust` with `client_request_id` (idempotent)
- Snapshot: `GET /api/inventory`
- Export: `GET /api/exports/inventory.xlsx` (Management/Devs)
- Sheet sync: enable with `GOOGLE_SHEET_SYNC_ENABLED=true` + sheet id + service account JSON

Config for the browser is [`config.js`](config.js) (`apiBaseUrl: '/api'`).

---

## Cutover runbook (from Apps Script / Sheets)

1. **Start stack** — `docker compose up -d --build`
2. **Import inventory/history** from legacy web app:
   ```bash
   docker compose exec -e LEGACY_WEBAPP_URL='https://script.google.com/.../exec' api \
     python -m app.import_legacy
   ```
3. **Import operators** (optional) — write gitignored `backend/seed_secrets.json`, then:
   ```bash
   docker compose exec -w /app api python -m app.import_operators
   ```
   Mount or copy the file into the container first.
4. **Point purchasing** at the mirror workbook (tabs: `Inventory`, `OpenCheckouts`, `Movements`) and enable sheet sync env vars.
5. **Freeze** the old Apps Script web app (disable writes).
6. **Backup Postgres** regularly:
   ```bash
   docker compose exec db pg_dump -U supply supply > backup-$(date +%F).sql
   ```

---

## Google Sheets mirror

Set in `.env` / compose:

```bash
GOOGLE_SHEET_SYNC_ENABLED=true
GOOGLE_SHEET_ID=your-sheet-id
GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
SHEET_SYNC_INTERVAL_MINUTES=10
```

Pre-create tabs named `Inventory`, `OpenCheckouts`, `Movements`. Share the sheet with the service account email. Humans should treat the sheet as **read-only**.

Manual sync: `POST /api/sync/sheets`

---

## API tests

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

---

## Folder structure

```
supply-room-kiosk/
├── index.html                 # kiosk UI
├── config.js                  # API base URL
├── src/                       # frontend modules
├── backend/                   # FastAPI + inventory services
│   ├── app/
│   └── tests/
├── docker-compose.yml         # db + api + kiosk
├── Dockerfile                 # nginx UI
└── nginx.conf                 # proxies /api → api:8000
```

---

## Development notes

- Credentials are **not** stored in `index.html` anymore.
- Manager Report requires Management or Devs session.
- Client availability checks are advisory; the API enforces stock under row locks.
- Retries after timeout are safe when the same `client_request_id` is reused.
