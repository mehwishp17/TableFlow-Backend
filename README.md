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

## Orders & Kitchen (KDS)

All routes are under `/api/v1`, answer in the `{ data, error, status }` envelope and need a JWT.

| Method & path | Admin | Staff | Kitchen | Platform |
|---------------|-------|-------|---------|----------|
| `GET /orders` | all orders of the restaurant | all active orders of the restaurant (not only their own; never finished ones) | active queue, oldest first | orders of all restaurants |
| `POST /orders` | yes | yes | 403 | 403 |
| `PATCH /orders/{order_id}/items/{item_id}` | 403 (view-only KDS) | 403 | yes | 403 (no KDS) |

Without a valid token every route answers 401.

**Create an order**

```
POST /api/v1/orders
{"order_type": "Dine-In", "table": 3, "items": [{"menu_item": 5, "quantity": 2}]}

POST /api/v1/orders          (Parcel: no table)
{"order_type": "Parcel", "items": [{"menu_item": 5, "quantity": 1}]}
```

The restaurant and waiter come from the logged-in user. A Dine-In order needs a table of the same
restaurant and a Parcel order must not have one. Dishes must exist in the restaurant and be
available; each dish may appear once (raise `quantity` instead; 1 to 99). The unit price is copied
from the menu when the order is created, `total_price` is the sum of price x quantity, and the
order and its items are saved in one transaction. The table status is not changed. Order ids look
like `ORD-7K3F9Q2X`.

**List orders** — query parameters:

| Parameter | Meaning |
|-----------|---------|
| `status` | `Pickup`, `Processed` or `Complete`: orders with at least one item in that state |
| `active` | `true` (default): orders with an item not yet `Complete`; `false`: finished orders; `all`: both. Kitchen and Staff users always get `true` |
| `table` | table id |
| `date_from`, `date_to` | `YYYY-MM-DD`, inclusive, server time zone (UTC) |
| `search` | matches the order id or the table number |

**How an order's state is represented.** There is no overall order status. Every order has
`is_active` (true while any item is not `Complete`) and `status_summary`, the number of its items in
each documented state, e.g. `{"Pickup": 1, "Processed": 0, "Complete": 1}`. An order with items in
several states therefore matches several `status=` filters. Each item has `kitchen_status`
(`Pickup` when created).

```
PATCH /api/v1/orders/ORD-7K3F9Q2X/items/11
{"kitchen_status": "Processed"}
```

Any of the three values may be chosen (the KDS shows a dropdown). The response is the whole order.
An item that does not belong to the order, or an order of another restaurant, gives 404.

**Notes**

- `orders.restaurant` is not in section 34 of the specification. It was added so that every order,
  including Parcel orders without a table, belongs to exactly one restaurant (tenant isolation).
- Staff can create an order for any table of their own restaurant and see all active orders of
  their own restaurant, not only the ones they created. Staff never see finished orders, even
  with `active=all` or `active=false`. There is no table-assignment model
  (confirmed by the team lead). Orders of other restaurants are never visible.
- The KDS is meant to poll `GET /orders` every 15 seconds. WebSockets (Django Channels) are not
  set up and not implemented.
- Section 32 lists `POST /kitchen/call-staff`. The team lead confirmed the `/calls/` routes below
  instead, so there is no `/kitchen/call-staff` route.

## Waiter calls, staff, scorecards, restaurant profile and printer settings

All routes are under `/api/v1`, answer in the `{ data, error, status }` envelope and need a JWT,
except `POST /calls/customer/`. These routes **end with a slash** (as confirmed by the team lead);
without it `POST` requests are not routed.

| Method & path | Who | Purpose |
|---------------|-----|---------|
| `POST /calls/customer/` | public (QR token) | A guest calls a waiter from the table's QR page |
| `POST /calls/` | Kitchen | Call one online waiter of the restaurant |
| `GET /calls/?status=` | Staff, Kitchen, Admin | `PENDING` (default), `PICKED`, `RESOLVED` or `all` |
| `PATCH /calls/{id}/pick/` | Staff | `PENDING` to `PICKED` (assigned to the caller) |
| `PATCH /calls/{id}/resolve/` | Staff (the one who picked it); Admin (any picked call of the restaurant) | `PICKED` to `RESOLVED`. A waiter's resolve credits the score; an Admin's does not |
| `PATCH /calls/{id}/cancel/` | Admin | `PENDING` or `PICKED` to `CANCELLED` (false alarm or abandoned call); no score |
| `GET` / `POST /staff/` | Admin | List / create Staff and Kitchen accounts |
| `GET` / `PATCH` / `DELETE /staff/{id}/` | Admin | Read / update / deactivate one account |
| `GET /staff/online/` | Admin, Kitchen | Online waiters of the restaurant |
| `GET /staff/me/scorecard/` | Staff | The waiter's own scorecard |
| `GET /staff/{id}/scorecard/` | Admin (any waiter of the restaurant), Staff (own id only) | One scorecard |
| `GET /staff/scorecards/` | Admin | Scorecards of all waiters, best first |
| `GET` / `PATCH /restaurant/profile/` | Admin | Profile, plan and live counters |
| `GET` / `PATCH /restaurant/printer-settings/` | Admin (`GET` also Kitchen) | Local folder used by `print.py` |
| `POST /auth/logout` | any role | Marks the user offline |

Every endpoint, with its request and response schema and permissions, is documented in Swagger
(`/api/docs/`, tags Calls, Staff, Scorecards, Restaurant, Auth).

**Call lifecycle.** `PENDING` to `PICKED` to `RESOLVED`, only forwards; anything else is `409`.
A picked call leaves the pending queue (`GET /calls/` default) and appears under `status=PICKED`
for the waiter who took it. The server always assigns the authenticated caller, nothing in the
request body can name another waiter. A customer call is open to every waiter of the
restaurant; a kitchen call is addressed to one waiter and only that waiter can pick it (`403`
for others). A table has at most one open customer call: pressing again returns the open one
(`200` instead of `201`). An open call can never get stuck: the restaurant Admin can cancel any
open call, or close a `PICKED` one on the waiter's behalf (recorded in `closed_by`, no score), and
when a waiter is deactivated (or stops being Staff) their `PICKED` customer calls go back to
`PENDING` so another waiter can take them, while kitchen calls addressed to them are `CANCELLED`
(only that waiter could take them, so the kitchen calls someone else). The public route needs the table's QR `token`; `restaurant_id` and
`table` of the QR link are optional but must match it, and every mismatch gives the same `404`
as an unknown token. It is rate limited (`customer_call`, 30 per minute per IP).

**Online status.** A user is online when the login flag is set (login sets it, `POST
/auth/logout` clears it) **and** an authenticated request was seen in the last
`ONLINE_TIMEOUT_SECONDS` (default **120**, set it in `.env`). Every authenticated request
counts as a heartbeat (so a staff panel polling `GET /calls/` stays online), written at most
every `ACTIVITY_TOUCH_INTERVAL_SECONDS` (default 30). A stale token after logout cannot bring a
user back online. There is no separate shift model: "on shift" means "online".

**Staff management.** "Staff" are the restaurant's Staff (waiter) and Kitchen accounts; Admin and
Platform accounts are never listed or changed, and the restaurant always comes from the Admin.
`DELETE` is a soft delete: the account is deactivated (it cannot log in and its tokens stop
working) but kept, because orders, calls and scores reference it. `PATCH is_active=true`
reactivates it. The ids `online`, `me` and `scorecards` are reserved. No password or hash is ever
returned.

**Scoring** (`ScoreEvent` ledger, one row per credit, shared by the personal scorecard and the
Admin analytics):

| Event | Points | When |
|-------|--------|------|
| Dine-In order created by a Staff waiter | +10 | At the moment the order is created (`POST /orders`) |
| Call picked and resolved | +5 | When the call becomes `RESOLVED` |
| Call picked within 2 minutes of `triggered_at` | +5 bonus | Same moment (`picked_at - triggered_at <= 2 min`, inclusive) |

Unique database constraints allow each credit once per order or call, so resolving twice,
retries and races cannot score twice. Parcel orders, orders created by an Admin, and calls that
are only picked, cancelled, or closed by an Admin earn nothing. A scorecard returns `total_score`, `dine_in_orders_served`,
`calls_resolved`, `bonus_points` and a `breakdown` per kind, plus `is_active`. The Admin
leaderboard lists every active waiter and any deactivated waiter who earned points (marked
`is_active: false`); a deactivated waiter with no points is left out. Nothing is deleted, and
`GET /staff/{id}/scorecard/` still works for them. Orders created before this feature have no
ledger rows and therefore no points.

**Rate limiting behind a proxy.** The public routes (`POST /calls/customer/`, `GET /qr/resolve/`)
are limited per client address, 30 requests per minute. Behind reverse proxies, that address is
taken from the `X-Forwarded-For` header. `NUM_PROXIES` (default **1**, set it in `.env`) tells DRF
which entry to use, counted from the right. Without the setting DRF uses the whole header, which a
client can change to get a fresh limit every time. Use `0` for local development.

**The default of 1 is an assumption and has not been verified on Render.** How many proxies are in
front of the deployed service, and what each adds to the header, cannot be checked from the code.
If the value is too low, unrelated visitors can share one limit; if it is too high, a client may be
able to choose the address and avoid the limit. Check it after deploying, by finding where the real
client address appears in the header (log only the number of entries and that position, never full
headers or tokens). The counters are kept in each server process's memory (no shared cache is
configured), so with several Gunicorn workers or instances each one counts separately. A "the 31st
request must return `429`" check is therefore not a reliable test there.

**Restaurant profile.** `name`, `address` and `logo_url` (http/https link; uploads are not
supported) can be edited. `subscription_plan` is read only (managed by the Platform). The
counters (`users`, `staff`, `kitchen_terminals` and `tables`) are counted from the database;
only active accounts count.

**Printer settings and `print.py`.** The Admin saves the **absolute folder path on the restaurant
PC** (for example `C:\TableFlow\KOT`, a UNC path or `/var/spool/tableflow`). The cloud server
only stores that text, it never opens, creates or checks the folder, because only the local
machine can reach it. The path is validated for format (absolute, at most 260 characters, no
`..`, control characters or `< > " | ? *`); an empty value means "not configured". `print.py`
runs on the restaurant PC and:

1. logs in with a Kitchen account (`POST /auth/login`);
2. reads the folder with `GET /restaurant/printer-settings/` (Kitchen may read it, only the Admin
   may change it);
3. polls `GET /orders` (the Kitchen queue) every few seconds and writes a ticket file for each
   new order into that folder, where the printer software picks it up.

`print.py` itself is not part of this repository.

## Testing

Automated: 402 tests (Login, error logging, Menu, Tables and QR, Orders and Kitchen, waiter calls,
staff and online status, scorecards, restaurant profile, printer settings, rate limits and the
Swagger schema). Run them without PostgreSQL (in-memory SQLite) with
`python manage.py test --settings=config.settings_test`, or on your local PostgreSQL with
`python manage.py test` (the database user must be allowed to create the test database).

Manual end-to-end check against PostgreSQL 18 (`tableflow_db`): 79 of 79 checks passed
(table CRUD and status changes, duplicate and constraint validation, restaurant isolation,
role permissions, A4 PDF with 7 tables on 2 pages, public QR resolver). All 22 migrations were
applied to PostgreSQL.

## Status

Implemented: custom User model, Restaurant model, JWT Login and logout, online status, staff
management, Menu APIs, Table APIs, QR PDF export, the public QR resolver, Order APIs, the Kitchen
item-status API, waiter calls (`/calls/`), scorecards, the restaurant profile and printer settings.

Not implemented: a token refresh endpoint (the access token lasts 15 minutes), WebSockets, and the
`print.py` script (not part of this repository).

Open items for production:

- A printed QR code has not yet been scanned with a phone.
- The rate limits (429) and `NUM_PROXIES` have not been checked on the deployed server.
- Set `FRONTEND_BASE_URL` to the public frontend URL before exporting QR PDFs.
- Set `CORS_ALLOWED_ORIGINS` to the deployed frontend's address.
- Set a real `DJANGO_SECRET_KEY` (at least 32 characters) and `DEBUG=False`.
- Every deployment must run `python manage.py migrate`. Login needs the `last_seen` column added by
  `accounts.0002`.
