# DOF.tool

Samlet PWA + API-platform, der forener:

- DOF.not (sjældenhedsnotifikationer + admin)
- Fugleliga (års-ranglister)
- Trend-modul (nu vs. historik)
- Turmodul (opret/join fugleture)
- Læringsportal (flashcards for udseende/kald)

## Stack

- FastAPI + Uvicorn
- Redis
- PostgreSQL
- PWA frontend (manifest + service worker)

## Hurtig opstart

```bash
docker compose up --build
```

API kører på `http://localhost:8000/api/v1`  
Frontend kører på `http://localhost:8000/`

Hvis API ikke svarer med det samme, følg containeren til health bliver `healthy`:

```bash
docker compose ps
docker compose logs -f api
```

## Lokalt uden Docker

Kræver Python 3.11+, samt kørende PostgreSQL og Redis (fx `docker compose up -d postgres redis`).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
uvicorn backend.app.main:app --reload
```

## Tests

```bash
pytest
```

## Login (obligatorisk)

Al adgang til app og API kræver login med en DOFbasen-konto. Login valideres mod
`POST https://krydslister.dofbasen.dk/api/v1/login` (samme endpoint som de gamle apps), og
observatørens navn hentes fra `https://dofbasen.dk/popobser.php`. Adgangskoden gemmes aldrig —
kun obserkode og navn persisteres, og sessionen holdes i en signeret cookie (`dof_session`).
Sæt `SESSION_SECRET` (og `SESSION_COOKIE_SECURE=true` bag HTTPS) i produktion.

Maskine-til-maskine-endpoints (`/api/update`, `/api/request_sync`) bruger `X-Admin-Key` i stedet
for en session.

## API-endpoints (v1)

- `GET /api/v1/health` (offentlig)
- `POST /api/v1/auth/login` (offentlig)
- `GET /api/v1/auth/session` (offentlig)
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/logout`
- `POST /api/v1/observations/ingest`
- `POST /api/v1/observations/legacy-update` (kompatibel med gammel DOF.not watcher payload)
- `POST /api/v1/observations/sync/dof`
- `POST /api/v1/observations/preferences`
- `GET /api/v1/observations/alerts?user_id=...`
- `GET /api/v1/observations/threads`
- `GET /api/v1/observations/thread/{day}/{thread_id}`
- `GET /api/v1/observations/summary`
- `GET /api/v1/rankings/yearly`
- `GET /api/v1/rankings/matrix`
- `GET /api/v1/trends/signals`
- `GET /api/v1/trips`
- `POST /api/v1/trips?creator_user_id=...`
- `POST /api/v1/trips/{trip_id}/join`
- `GET /api/v1/learning/flashcards`
- `POST /api/v1/learning/flashcards`
- `GET /api/v1/learning/flashcards/quiz`
- `GET /api/v1/admin/overview`
- `POST /api/v1/admin/blacklist` (`X-Admin-Key`)
- `DELETE /api/v1/admin/blacklist/{user_id}` (`X-Admin-Key`)
- `GET /api/v1/admin/blacklist` (`X-Admin-Key`)
- `GET /api/v1/admin/traffic/summary` (`X-Admin-Key`)
- `GET /api/v1/admin/admins` (`X-Superadmin-Key`)
- `POST /api/v1/admin/admins` (`X-Superadmin-Key`)
- `DELETE /api/v1/admin/admins/{user_id}` (`X-Superadmin-Key`)
- `POST /api/v1/admin/pageviews`

## Legacy watcher-integration

Eksisterende watcher kan pege på:

```bash
SERVER_URL=http://localhost:8000/api/v1/observations/legacy-update
```

eller du kan trigge DOF-hentning direkte i backend:

```bash
curl -X POST http://localhost:8000/api/v1/observations/sync/dof \
  -H 'content-type: application/json' \
  -d '{"target_date":"2026-08-13"}'
```

## Næste skridt

1. Koble `watcher`-flowet fra gamle DOF.not direkte på `/api/v1/observations/ingest`.
2. Tilføj scheduler-jobs (Redis) til løbende sync af observationer/phenologi.
3. Udbyg roller (admin/superadmin) oven på den nye session-baserede auth.
4. Migrér gamle adminhandlinger (blacklist, trafik, brugeradministration) trinvis.
