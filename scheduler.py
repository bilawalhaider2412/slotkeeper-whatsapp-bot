"""
SlotKeeper reminder worker.

Two ways to run it:
1. EMBEDDED (default): app.py starts process_due() in a background thread,
   so a single web deploy handles reminders with zero extra config.
2. STANDALONE: `python3 scheduler.py` as a separate worker process (only
   needed if you scale beyond one web worker).

Behavior:
- WhatsApp Cloud API configured -> sends automatically.
- Not configured -> marks the job 'manual' so the owner sees the
  ready-to-send text on the dashboard (honest manual mode).
- Before every send it re-checks: booking still Confirmed, still in the
  future, customer hasn't cancelled. Stale jobs expire instead of piling up.
- Restarts are safe: jobs are durable in SQLite.
"""

import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
from bot import reminder_draft

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v24.0")
POLL_SECONDS = int(os.environ.get("SCHEDULER_POLL_SECONDS", "60"))

try:
    import requests
except ImportError:
    requests = None


def send_whatsapp(to_number, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID or not requests:
        return False
    url = (f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
           f"{PHONE_NUMBER_ID}/messages")
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}",
                     "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": to_number,
                  "type": "text", "text": {"body": text}},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"  send failed: {exc}", flush=True)
        return False


def process_due():
    due = db.due_reminders()
    if not due:
        return 0
    for r in due:
        # Recheck everything before sending (spec: validate at send time)
        booking = db.get_booking(r["booking_id"])
        if not booking or booking["status"] != "Confirmed":
            db.mark_reminder(r["id"], "cancelled", "booking no longer confirmed")
            print(f"  reminder #{r['id']}: cancelled (booking not confirmed)", flush=True)
            continue
        try:
            appt = datetime.fromisoformat(booking["appointment_at"])
            if not appt.tzinfo:
                appt = appt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            db.mark_reminder(r["id"], "failed", "bad appointment time")
            continue
        if appt <= datetime.now(timezone.utc):
            db.mark_reminder(r["id"], "expired", "appointment already passed")
            continue
        text = reminder_draft(booking)
        if send_whatsapp(booking["phone"], text):
            db.mark_reminder(r["id"], "sent")
            print(f"  reminder #{r['id']} ({r['kind']}): sent to {booking['phone']}", flush=True)
        else:
            db.mark_reminder(r["id"], "manual", "API not configured - send from dashboard")
            print(f"  reminder #{r['id']} ({r['kind']}): queued for manual send", flush=True)
    return len(due)


def main():
    db.init_db()
    mode = "AUTO (WhatsApp API)" if (WHATSAPP_TOKEN and PHONE_NUMBER_ID) else "MANUAL (dashboard drafts)"
    print(f"SlotKeeper scheduler started - {mode}, polling every {POLL_SECONDS}s", flush=True)
    while True:
        try:
            n = process_due()
            if n:
                print(f"processed {n} due reminder(s)", flush=True)
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
