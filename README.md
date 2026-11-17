# TableFlow – Smart Restaurant Management System

TableFlow is a QR-code-driven, multi-tenant restaurant management SaaS platform. It connects
dine-in customers, restaurant admins, waitstaff, kitchen personnel and platform administrators.

Operational loop: QR scan / manual order → Kitchen Display System → waiter notification &
pickup → table service & billing → analytics & reporting.

Authenticated roles: **Admin**, **Staff**, **Kitchen**, **Platform**.

The primary specification is [docs/TableFlow_Project_Documentation.pdf](docs/TableFlow_Project_Documentation.pdf).

## Technology stack

| Layer | Technology |
|-------|-----------|
| Backend | Python, Django, Django REST Framework |
| Auth | SimpleJWT (access + refresh tokens) |
| Database | PostgreSQL |
| Cache / real-time | Redis, Django Channels |
| Server | Gunicorn, Docker (deployment phase) |
| Frontend | React (Vite) – developed separately in `frontend/` |

## Project structure

```
TableFlow/
├── backend/
│   ├── config/        Django project config (settings, urls, asgi, wsgi)
│   ├── accounts/      Users, roles, authentication
│   ├── restaurants/   Restaurant (tenant) profile and subscription
│   ├── menus/         Categories and menu items, dish availability
│   ├── tables/        Tables, occupancy status, QR codes
│   ├── orders/        Orders and order items
│   ├── kitchen/       KDS, item prep status, waiter calls
│   ├── analytics/     Revenue and staff-performance reporting
│   ├── printing/      Thermal printer / KOT integration
│   ├── manage.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/          React app (separate from backend)
├── docs/              Project documentation (PDF)
├── README.md
└── .gitignore
```

## Initial setup (Windows PowerShell)

```powershell
cd backend

# 1. Create and activate a virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
copy .env.example .env
# edit .env: set DJANGO_SECRET_KEY and your PostgreSQL credentials

# 4. Verify the project, then start the server
python manage.py check
python manage.py runserver
```

On macOS/Linux activate with `source venv/bin/activate` and copy with `cp`.

The server runs at http://127.0.0.1:8000/. PostgreSQL must be running and the database named in
`.env` must already exist.

Generate a secret key with:

```
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
```

## Login API

`POST /api/v1/auth/login` with `{"id": "...", "password": "...", "role": "Admin|Staff|Kitchen|Platform"}`.
Returns `{data: {id, role, access, refresh}, error, status}`. See `accounts/`.

Run the tests (no PostgreSQL needed; uses in-memory SQLite):

```powershell
python manage.py test --settings=config.settings_test
```

## Table & QR codes

Tables: `/api/v1/tables` (Admin full CRUD; Staff read + `PATCH /tables/{id}/status`).
`GET /api/v1/tables/qr-pdf` (Admin) downloads an A4 PDF, six QR cards per page.

Each QR code opens `<FRONTEND_BASE_URL>/menu?restaurant_id={id}&table={id}&token={uuid}`.
The frontend should call the public `GET /api/v1/qr/resolve/?token={uuid}` and check that the
returned `restaurant_id` and `table_id` match the URL before showing the menu.

**Production configuration:** set `FRONTEND_BASE_URL` (no secret, e.g. `https://app.example.com`)
in the hosting environment *before* exporting QR PDFs. The default is the local dev server, and
QR codes printed with a wrong base URL must be reprinted. `python manage.py check` warns when
DEBUG is off and the URL still points at localhost.

**Rate limiting:** the resolver is limited to 30 requests/minute per client IP (`qr_resolve` in
`REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']`). The counters live in Django's default in-process
cache, so with several Gunicorn workers each worker counts separately (the effective limit is
roughly workers x 30/min) and counters reset on restart. A shared cache (e.g. Redis via
`CACHES`) would make the limit exact; none is configured in this repository yet.

### Table API reference

All table endpoints are under `/api/v1`, use the `{ data, error, status }` envelope and need
`Authorization: Bearer <access token>`, except the QR resolver. A table always belongs to the
restaurant of the logged-in user; other restaurants' tables return 404.

| Method & path | Admin | Staff | Kitchen / Platform |
|---------------|-------|-------|--------------------|
| `GET /tables` (filters `status`, `table_type`) | yes | yes | 403 |
| `GET /tables/{id}` | yes | yes | 403 |
| `POST /tables` | yes | 403 | 403 |
| `PATCH /tables/{id}` (details) | yes | 403 | 403 |
| `DELETE /tables/{id}` | yes | 403 | 403 |
| `PATCH /tables/{id}/status` | yes | yes | 403 |
| `GET /tables/qr-pdf` (A4 PDF, 6 cards per page) | yes | 403 | 403 |
| `GET /qr/resolve/?token={uuid}` (public, rate limited) | public | public | public |

Without a token every protected endpoint answers 401.

Examples:

```
POST /api/v1/tables
{"table_number": "T1", "table_type": "Indoor", "capacity": 4}

PATCH /api/v1/tables/1/status
{"status": "Occupied"}
```

`table_type`: `Indoor`, `Outdoor`, `VIP`, `Bar`. `status`: `Available`, `Occupied`, `Reserved`,
`Cleaning`. `table_number` is a label (letters, digits, `.`, `_`, `-`) that must be unique per
restaurant, ignoring case. The QR token is never returned by the table API.

## Testing

Automated (no PostgreSQL needed; in-memory SQLite): 114 tests, run with
`python manage.py test --settings=config.settings_test`.

Manual end-to-end check against PostgreSQL 18 (`tableflow_db`): 79 of 79 checks passed
(table CRUD and status changes, duplicate and constraint validation, restaurant isolation,
role permissions, A4 PDF with 7 tables on 2 pages, public QR resolver). All 22 migrations were
applied to PostgreSQL. The automated suite itself has not been run on PostgreSQL.

## Status

Implemented: custom User model, Restaurant model, JWT Login API, Menu APIs, Table APIs,
QR PDF export and the public QR resolver. Orders, Kitchen, Analytics and Printing are pending.

Open items before production:

- A printed QR code has not yet been scanned with a phone.
- The QR resolver rate limit (429) has not been tested.
- Set `FRONTEND_BASE_URL` to the public frontend URL before exporting QR PDFs.
- Set a real `DJANGO_SECRET_KEY` (at least 32 characters) and `DEBUG=False`.
- Deployment to Render is pending: set the environment variables above, `ALLOWED_HOSTS` and the
  database variables, then run `python manage.py migrate`.
