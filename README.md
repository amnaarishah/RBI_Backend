# AMS / RBI backend

FastAPI + SQLAlchemy + PostgreSQL. Settings come from `backend/.env` (see `.env.example`; the real `.env` is not in git).

## Run
```
python -m uvicorn app.main:app --reload --port 8000
```
Database changes are applied with `alembic upgrade head`.

## Sign-in (currently OFF)
Login is switched off during development. To turn it on for deployment set `AUTH_REQUIRED=true` in `backend/.env`
and `NEXT_PUBLIC_AUTH_REQUIRED=true` in `frontend/.env.local`. When on, every API call except `/api/v1/auth/login`
needs a token. Create the first administrator once, from `backend/`:
```
python -m app.seed.create_user --username <name> --role admin --full-name "<Full name>"
```
The password is asked for interactively (at least 10 characters). More users can be added the same way or by an
administrator through `POST /api/v1/auth/users`. Roles:

| role | can do |
|---|---|
| viewer | read everything, run the t_min calculator |
| engineer | + create / edit equipment and inspections, imports, document uploads |
| admin | + delete records, manage users |

Tokens last
`ACCESS_TOKEN_EXPIRE_MINUTES` (default 480). The signing key is created once in `backend/.secret_key` (not in git)
unless `SECRET_KEY` is set.

## Tests
```
python -m unittest discover -s tests
```
They use throw-away databases (`ams_test_*`) on the same server and never touch the real one.
Formula tests follow the client's sheets (`T min calculation.xlsx`, `Equipment Register-Rev02.xlsx`,
`tank calculations.xlsx`).

## Material stress data (ASME II-D Table 1A / 1B)
`app/seed/seed_full_pdf.py` extracts the tables from the PDF and finishes with `app/seed/backfill_asme_metadata.py`
(clean spec / grade / UNS / page / line columns). Run the backfill alone (`python -m app.seed.backfill_asme_metadata
--apply`) to repair or extend an existing table; without `--apply` it is a dry run.
