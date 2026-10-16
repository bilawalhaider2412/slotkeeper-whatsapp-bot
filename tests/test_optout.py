"""Opt-out (STOP) handling tests. Run:  cd ~/workspace/bots/slotkeeper && python3 tests/test_optout.py"""
import hashlib
import hmac
import json
import os
import sys
from base64 import b64encode
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SLOTKEEPER_DB", "/tmp/slotkeeper-optout-test.db")
os.environ["OWNER_USER"] = "admin"
os.environ["OWNER_PASSWORD"] = "t123"
os.environ["VERIFY_TOKEN"] = "secret"
os.environ["APP_SECRET"] = "topsecret"
os.environ["MISSED_CALL_SECRET"] = "notifier-secret"
os.environ["SCHEDULER_IN_WEB"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.path.exists(os.environ["SLOTKEEPER_DB"]):
    os.remove(os.environ["SLOTKEEPER_DB"])

import app as appmod  # noqa: E402
import db  # noqa: E402
from scheduler import process_due  # noqa: E402

client = appmod.app.test_client()
auth = {"Authorization": "Basic " + b64encode(b"admin:t123").decode()}

sent = []
appmod.send_whatsapp = lambda to, text: sent.append((to, text)) or {"sent": True}


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    print(f"ok - {name}")


def webhook_text(sender, text):
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": sender, "type": "text", "text": {"body": text}}]}}]}]}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    return client.post("/webhook", data=body, content_type="application/json",
                       headers={"X-Hub-Signature-256": sig})


def last_reply(to):
    return [t for (p, t) in sent if p == to][-1]


# --- STOP opts out, confirmation sent, normal flow skipped ---
r = webhook_text("923001111111", "STOP")
check("webhook accepts signed payload", r.status_code == 200)
check("opt-out recorded", db.is_opted_out("923001111111"))
check("opt-out confirmation sent", "won't receive messages" in last_reply("923001111111"))

# --- Roman Urdu stop phrase also works ---
webhook_text("923002222222", "band karo")
check("urdu stop phrase works", db.is_opted_out("923002222222"))

# --- opted-out sender is met with silence ---
n = len(sent)
webhook_text("923001111111", "book")
check("silence for opted-out sender", len(sent) == n)

# --- START opts back in ---
webhook_text("923001111111", "START")
check("opt-in clears flag", not db.is_opted_out("923001111111"))
check("opt-in confirmation sent", "Welcome back" in last_reply("923001111111"))

# --- missed-call text-back respects opt-out ---
db.set_opt_out("923004444444", True, source="test")
r = client.post("/api/missed-call",
                json={"caller_phone": "923004444444", "secret": "notifier-secret"})
d = r.get_json()
check("missed call skipped for opted-out", d["sent"] is False and d.get("reason") == "opted out", str(d))

# --- scheduler skips reminders for opted-out customers ---
appt = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")
bid = db.create_booking("923005555555", "Opt Out", "Haircut", "PKR 500",
                        "20 Oct", "5pm", appt)
db.set_booking_status(bid, "Confirmed")
db.set_opt_out("923005555555", True, source="test")
import sqlite3
c = sqlite3.connect(os.environ["SLOTKEEPER_DB"])
c.execute("UPDATE reminders SET send_at = datetime('now', '-1 minute') WHERE booking_id = ?", (bid,))
c.commit(); c.close()
process_due()
st = {x["kind"]: (x["status"], x["note"]) for x in db.list_reminders() if x["booking_id"] == bid}
check("scheduler cancels opted-out reminder",
      any(s == "cancelled" and "opted out" in (note or "") for s, note in st.values()), str(st))

# --- manual notify blocked for opted-out ---
r = client.post(f"/api/bookings/{bid}/notify", json={"kind": "confirmation"}, headers=auth)
check("manual notify blocked", r.status_code == 409 and "opted out" in r.get_json()["error"])

# --- dashboard opt-out APIs ---
r = client.get("/api/opt-outs", headers=auth)
phones = [o["phone"] for o in r.get_json()]
check("opt-outs listed", "923002222222" in phones and "923005555555" in phones, str(phones))
r = client.post("/api/opt-outs/clear", json={"phone": "923002222222"}, headers=auth)
check("owner can re-allow", r.status_code == 200 and not db.is_opted_out("923002222222"))
r = client.get("/api/opt-outs")
check("opt-outs require auth", r.status_code == 401)

# --- dashboard renders new section ---
r = client.get("/dashboard", headers=auth)
check("dashboard shows opt-outs", r.status_code == 200 and b"Opted out" in r.data)

print("\nAll opt-out checks passed.")
