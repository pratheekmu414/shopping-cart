# Checkout & Rewards Service

## Prerequisites

- Python 3.10+
- Docker

## Setup

Start Postgres 17 on `localhost:5433`. This creates two databases: `shop` for the app and `shop_test` for the tests.

```bash
docker compose up -d --wait
```

Install the dependencies:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run

The app creates its tables and seeds the products on startup.

```bash
uvicorn app.main:app --port 8000
```

Then open http://localhost:8000/docs.

To run several instances against the same database:

```bash
uvicorn app.main:app --port 8000 --workers 4
```

## Test

The tests run against the `shop_test` database.

```bash
pytest -q
```

## Configuration

| Variable | Default |
|---|---|
| `CURRENCY` | `INR` (ISO 4217 code; amounts are in its minor unit) |
| `COUPON_EVERY_N` | `5` |
| `COUPON_PERCENT` | `10` |
| `DATABASE_URL` | `postgresql://shop:shop@localhost:5433/shop` |
| `TEST_DATABASE_URL` | `postgresql://shop:shop@localhost:5433/shop_test` |

## Reset / stop

Reset to seed data:

```bash
docker compose down -v && docker compose up -d --wait
```

Stop Postgres:

```bash
docker compose down
```
