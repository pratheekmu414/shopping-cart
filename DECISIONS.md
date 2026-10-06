# Design Decisions

| | |
|---|---|
| **Stack** | Python, FastAPI, PostgreSQL 17 (Docker), `psycopg` 3 |
| **Time spent** | 4.25 hours |
| **API docs** | `/docs` (Swagger), `openapi.json` |
| **Concurrency** | Pessimistic locking. We lock a row before checking it, because many buyers fight over the same few items and the same coupon, so it's simpler to make them wait in line than to let them clash and retry. |

## Contents

1. [Invariants](#1-invariants)
2. [Ambiguities](#2-ambiguities)
3. [Decisions](#3-decisions)
4. [Concurrency and idempotency](#4-concurrency-and-idempotency)
5. [Money](#5-money)
6. [Errors](#6-errors)
7. [Scope](#7-scope)
8. [Scaling and weaknesses](#8-scaling-and-weaknesses)
9. [Use of AI](#9-use-of-ai)

```
app/
  db.py          schema, seed data, transaction helper
  errors.py      domain error type
  carts.py       products and carts
  checkout.py    checkout, orders, discount math
  coupons.py     coupon milestones, report
  schemas.py     request and response models
  api_docs.py    Swagger text and examples
  main.py        routes and error format
tests/
  conftest.py           fixtures and race helper
  test_concurrency.py   races and retries
  test_rules.py         business rules and money
  test_api.py           HTTP status and error codes
```

---

## 1. Invariants

Each rule is checked in code while holding a lock on the rows involved. The important ones are also database constraints, so a bug becomes a rejected write instead of bad data.

| Area | Invariant | Enforced by |
|---|---|---|
| **Inventory** | Stock never goes below 0; units sold never exceed units available | Lock the product, then check stock, only reduce stock if enough is left (backup), database rejects negative stock (last resort) |
| | Stock only goes down when an order is created; never on failure, never twice on retry | Same transaction as the order insert |
| **Cart** | Holds only real products with quantity > 0 | Request validation, foreign key, `CHECK (quantity > 0)` |
| | Open → checked out, one way only; a checked-out cart is frozen | Cart row lock, `UNIQUE (orders.cart_id)` |
| **Order** | One checkout plus all its retries makes at most one order | Cart row lock, replay of the existing order |
| | Never changes; keeps its own copy of names, prices, discount and total | Prices copied into `order_lines` |
| | Line totals add up to the subtotal; subtotal − discount = total ≥ 0 | Computed once, `CHECK` on line total, discount range and total |
| **Coupon** | Used by at most one order, even under races | Coupon row lock, `UNIQUE (orders.coupon_code)` |
| | Only used up if the checkout succeeds | "Used" means "an order references it" |
| | One coupon per milestone, only once reached | Advisory lock, `UNIQUE (coupons.milestone_order)` |
| | Keeps the % it was issued with | `percent` stored on the coupon |
| **Report** | Matches orders and coupons: net = gross − discounts (per currency); generated = available + redeemed | Computed from orders and coupons in one snapshot |
| | Reading it changes nothing | Read-only transaction |

> "Line totals add up to the subtotal" is checked in code only, not by the database.

---

## 2. Ambiguities

| Question | Choice |
|---|---|
| Price changes after adding to cart | Cart shows current prices. The order uses the price at checkout and keeps a copy. Optional `expected_total_minor` makes checkout fail with `409 TOTAL_CHANGED` if the total moved. |
| Stock drops after adding to cart | No reservation. Checkout re-checks; if any line is short the whole checkout fails (`409 INSUFFICIENT_INVENTORY`). |
| What counts as a retry? | The same cart. A cart becomes at most one order. |
| Retry with a different coupon | A new request: `409 CART_ALREADY_CHECKED_OUT`. |
| Do discounted orders count toward milestones? | Yes, every placed order counts. |
| Several milestones passed before the admin asks | Each is owed a coupon. One call issues one, oldest first. |
| Is a reached milestone optional? | No. It never expires; the admin call just issues it. |
| Who can use a coupon? | Anyone with the code (no customer accounts). |
| Coupon expiry | None. |
| Changing n or x | Future only. Old coupons keep their %. Next milestone = last rewarded + n. |
| What does the discount apply to? | The whole order subtotal. |
| Currency | One per store (`CURRENCY`, default `INR`). Every amount comes with its currency. Orders keep theirs. |
| Removing an item that isn't there | `200`, so retries are harmless. |
| Adding an item that's already there | `409 ITEM_ALREADY_IN_CART`; use `PUT`. A retried add can't double the quantity. |

---

## 3. Decisions

### 3.1 Row locks in Postgres, always in the same order

| | |
|---|---|
| **Context** | Checkout reads stock, coupon and cart, checks them, then writes. Two checkouts at once could both pass the checks and oversell or reuse a coupon. |
| **Options** | In-memory store with an app lock, one global lock for all writes, `SERIALIZABLE` with retries, row locks (`SELECT … FOR UPDATE`) |
| **Choice** | Row locks at `READ COMMITTED`. Checkout locks cart → products (by ID) → coupon. Cart edits lock the cart. Coupon generation uses an advisory lock. The report reads one snapshot. |
| **Why** | Only requests touching the same rows wait. A fixed lock order prevents deadlocks. Locks live in the database, so they work across app instances. `SERIALIZABLE` needs retry loops. |
| **Consequences** | A very popular product queues its own checkouts. Tests need Postgres running. |

### 3.2 The cart is the idempotency key

| | |
|---|---|
| **Context** | Clients retry checkout after timeouts. A retry must not create a second order. |
| **Options** | `Idempotency-Key` header with a stored-response table, the cart ID, no idempotency |
| **Choice** | The cart ID. Re-checking-out a placed cart with the same coupon returns the original order (`200` instead of `201`). |
| **Why** | A cart becomes at most one order anyway, so no extra table or key expiry. Failed checkouts write nothing, so retrying them just runs again. |
| **Consequences** | Two orders from the same items need two carts. Real payments would also need an `Idempotency-Key` header. |

### 3.3 No status columns; derive state from orders

| | |
|---|---|
| **Context** | If "checked out" and "coupon used" were flags, a failed checkout could leave one set by mistake. |
| **Options** | Status columns updated during checkout, derive both from orders |
| **Choice** | A cart is checked out if an order points to it. A coupon is used if an order points to it. |
| **Why** | Nothing to keep in sync. A failed checkout creates no order, so the coupon stays free automatically. "At most once" becomes a `UNIQUE` constraint. |
| **Consequences** | Coupon expiry or revocation would need a real status column. |

### 3.4 Live prices in the cart, copied at checkout

| | |
|---|---|
| **Context** | What happens when a price changes after an item is added? |
| **Options** | Lock the price when added; always use the current price silently; use the current price, but let the client confirm the total |
| **Choice** | The last one. The cart stores only product and quantity. Checkout copies current prices into the order. Optional `expected_total_minor` rejects a total the customer didn't see. |
| **Why** | Locked prices go stale. Silent repricing charges amounts the customer never saw. |
| **Consequences** | Clients that skip `expected_total_minor` accept current prices. |

### 3.5 No stock reservation in the cart

| | |
|---|---|
| **Context** | When should stock be claimed? |
| **Options** | Reserve on add, with expiry and cleanup; claim only at checkout |
| **Choice** | Claim at checkout. Adding to cart does a quick stock check for early feedback only. |
| **Why** | Reservations need timers, cleanup and abandoned-cart rules, and abandoned carts would lock up the 3 limited lamps. |
| **Consequences** | A customer can lose the race at checkout and gets a clear `409` naming the item. |

### 3.6 One coupon per admin call, tracked by order count

| | |
|---|---|
| **Context** | The spec doesn't say what happens if several milestones pass, or if n or x changes. |
| **Options** | Issue automatically at checkout, issue all owed coupons in one call, one per call |
| **Choice** | One coupon per call, for the oldest unrewarded milestone. Each coupon stores its milestone (unique) and its %. |
| **Why** | The spec says the admin requests generation. One per call is predictable; the unique milestone stops duplicates; a stored % means changing x never changes an issued coupon. |
| **Consequences** | A backlog needs several calls. Changing n can skip owed milestones (§8). |

### 3.7 Integer minor units with a currency code

| | |
|---|---|
| **Context** | No floats, predictable discounts, and the store may switch currency later. |
| **Options** | `Decimal`; integers named after one unit (`_cents`); integers named `_minor` plus an ISO 4217 code. Scope: currency per product or per store. |
| **Choice** | Integers in the smallest unit (`*_minor`) with `currency`, e.g. `{"price_minor": 8999, "currency": "INR"}` = ₹89.99. One currency per store; orders keep theirs; the report groups revenue by currency. |
| **Why** | Integers can't drift. A generic name plus a code works for paise, cents or yen. One currency per store means carts can't mix currencies. |
| **Consequences** | Changing `CURRENCY` re-labels prices without converting them. Several currencies at once would need a currency per product. |

### 3.8 Successful checkout means paid

| | |
|---|---|
| **Context** | Payment integration isn't required. |
| **Options** | Treat checkout as paid, add a fake payment gateway |
| **Choice** | A committed checkout is a paid order. |
| **Why** | A real payment call can't run inside a database transaction; a fake one inside it would hide that problem. |
| **Consequences** | Real payments would split checkout: reserve and create a pending order → charge → mark paid or release everything. |

---

## 4. Concurrency and idempotency

**One request = one transaction.** Any error rolls everything back, so a failed request changes nothing.

**Checkout, step by step**

1. Lock the cart. If it already has an order, return it.
2. Reject an empty cart.
3. Lock the products (by ID). Reject if any line is short.
4. Lock the coupon. Reject if unknown or used.
5. Work out the totals. Reject if they don't match `expected_total_minor`.
6. Write the order, reduce stock, write the order lines.
7. Commit.

---

## 5. Money

```
line_total = unit_price × quantity
subtotal   = Σ line_total
discount   = min(subtotal, round_half_up(subtotal × percent / 100))
total      = subtotal − discount          (always 0 ≤ total ≤ subtotal)
```

- All amounts are integers in the currency's smallest unit, with a `currency` code. No floats.
- The discount is rounded once per order, not per line.

| Subtotal | Percent | Discount |
|---|---|---|
| 999 | 10% | 100 (99.9 rounds up) |
| 5 | 10% | 1 (0.5 rounds up) |
| 4 | 10% | 0 |
| 1234 | 100% | 1234 (total 0) |

---

## 6. Errors

Every error has the same shape. Clients should branch on `code`.

```json
{"error": {"code": "INSUFFICIENT_INVENTORY", "message": "…", "details": {…}}}
```

| Status | Meaning | Codes |
|---|---|---|
| **404** | The cart, product or order doesn't exist | `CART_NOT_FOUND`, `PRODUCT_NOT_FOUND`, `ORDER_NOT_FOUND` |
| **409** | Valid request, but clashes with current state | `INSUFFICIENT_INVENTORY`, `CART_ALREADY_CHECKED_OUT`, `ITEM_ALREADY_IN_CART`, `COUPON_ALREADY_REDEEMED`, `TOTAL_CHANGED`, `NO_ELIGIBLE_MILESTONE` |
| **422** | The request itself is wrong | `VALIDATION_ERROR`, `CART_EMPTY`, `COUPON_NOT_FOUND` |

---

## 7. Scope

| Done | Deferred |
|---|---|
| Products, carts, checkout, orders | Authentication |
| Coupons and report | Coupon expiry, per-customer coupons |
| Admin endpoint to change price or stock | Stock reservations |
| Idempotent checkout | Real payments |
| Locking that works across instances | `Idempotency-Key` for other POSTs |
| Database constraints as a backstop | Pagination, migrations |
| 24 tests | Connection pooling, rate limiting |
| Swagger docs with examples | Logging and metrics |
| Currency on every amount | Several currencies at once |

---

## 8. Scaling and weaknesses

**For production I'd add**

- A connection pool (today each request opens a new connection).
- If thousands of people want the same product, send their orders to an event stream, which holds them in line so one worker can process them in order without flooding the database.
- An `idempotency_keys` table once real payments exist.
- Run reports on a read replica, or move them to an OLAP store (Redshift, ClickHouse or similar).
- Real migrations, an outbox (to prevent dual write failures), and lock timeouts (so requests stop waiting on a stuck lock).

**Known weaknesses**

- A popular product queues its own checkouts.
- Changing n with milestones still owed can skip them. Example: 11 orders under n = 5, nothing issued, then n becomes 3. Coupons come at 3, 6, 9; milestones 5 and 10 are lost. Issuing all owed coupons before changing n avoids this, but nothing enforces it.
- Coupon codes are short (32 random bits) and checkout has no rate limit.
- "Line totals add up to the subtotal" isn't a database rule.

---

## 9. Use of AI

Claude Code: code generation (98%+)

- I wrote the invariants list and made the initial code setup.
- I opted for a pessimistic concurrency control approach (lock rows before checking them) and decided how the services are wired together: separate modules for carts, checkout and coupons, Postgres database setup all generated by Claude Code.
- The AI first computed milestones as "next multiple of n". I chose "last rewarded + n" in my notes and had the code and a test changed to match.
- The AI completely generated the test suite, I verified if any invariant has been skipped.
- I validated the behavior when each lock is removed. That showed the coupon race test was too weak (every racer bought the same product, so a different lock hid the bug), and I had it fixed.
- The AI formatted the DECISIONS.md -> Decision section according to the context -> options -> choice -> reasoning -> consequences / side effects structure I provided.
