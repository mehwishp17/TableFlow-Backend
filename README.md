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

## Status

Implemented: custom User model, minimal Restaurant model, Login API. Everything else is pending.
