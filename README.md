# SlotKeeper WhatsApp Bot — bot + dashboard in one deployable app (Phase 2)

The WhatsApp booking bot and the owner dashboard, wired together.
A customer books on WhatsApp → the booking lands in the database →
the owner sees it on the dashboard, confirms it, and the customer
gets the confirmation. Reminders go out automatically at 24h and 2h.

## What's inside

| File | Purpose |
|---|---|
| `app.py` | Flask app: Meta webhook (`/webhook`, HMAC-verified) + owner dashboard (`/dashboard`) + JSON API |
| `bot.py` | Conversation logic: book, cancel, reschedule, FAQs, human handoff (English + Roman Urdu keywords) |
| `scheduler.py` | Reminder engine: ALSO runs embedded in the web app (background thread) — no separate worker needed |
| `db.py` | SQLite storage: bookings (UTC times), durable reminder jobs, waiting list |
| `templates/dashboard.html` | Owner dashboard: counts, booking log, confirm/cancel/reschedule, reminder queue, waiting list, CSV export |
| `simulate.py` | Terminal demo — no Meta account needed |
| `MASTER_SPEC.md` | The full production blueprint this is built against |

## Try it right now

```bash
cd ~/workspace/bots/slotkeeper
python3 simulate.py        # watch a booking land in the database
python3 app.py             # web app + embedded reminder scheduler
                           # dashboard at http://localhost:5000/dashboard
                           # login: admin / change-me  (change before selling!)
```
One deploy, one process: the web app serves the dashboard and webhook,
and a background thread inside it sends the 24h/2h reminders. No separate
worker to configure. (Keep gunicorn at `--workers 1` so reminders never
double-send.)

## Going live for a client

1. Client creates an app at developers.facebook.com, adds the **WhatsApp**
   product, gets a number (test number is free for the demo).
2. Deploy this folder to Render (`render.yaml` includes web + worker),
   Railway, or a VPS. Set the env vars from `.env.example`, including
   `APP_SECRET` (enables webhook signature verification).
3. In the Meta dashboard: webhook URL `https://YOUR-DOMAIN/webhook`,
   verify token = your `VERIFY_TOKEN`, subscribe to `messages`.
4. Give the client the dashboard URL + their login. Done.

> Hosting note: the embedded scheduler means web + reminders share one
> process and one SQLite file — this works on any single-server deploy
> (Render, Railway, one VPS). Only split them if you scale past one web
> worker, and then switch `db.py` to Postgres first.

## How the pieces fit (sell this story)

- **Customer side:** chats with the business on WhatsApp, bot handles
  greeting → services → booking → FAQs, 24/7.
- **Owner side:** opens the dashboard, sees "Awaiting confirmation"
  bookings, hits Confirm → customer gets the confirmation on WhatsApp
  automatically (or as ready-to-send text in manual mode).
- **Waiting list + reminders + CSV export** included — this is the
  "Pro" package from the pitch sheet.

## Honest notes

- Meta charges a small per-conversation fee on the Business API —
  fold it into the monthly retainer.
- SQLite is fine for one shop on one server; if a client outgrows it or
  needs web+worker on separate hosts, swap `db.py` for Postgres
  (one file to change).
- Change `OWNER_USER`/`OWNER_PASSWORD` per client before handover.
- Sessions are in-memory; a server restart drops half-finished
  conversations (customers just say "hi" again — acceptable for v1).
- Roman Urdu keywords are covered; full Urdu-script understanding is
  a later phase (see MASTER_SPEC.md).
- Webhook HMAC verification activates when `APP_SECRET` is set —
  without it the app logs a dev-mode warning and accepts (never ship
  that to production).
