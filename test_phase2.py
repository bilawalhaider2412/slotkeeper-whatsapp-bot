"""Phase-2 regression tests. Run:  cd ~/workspace/bots/slotkeeper && python3 tests/test_phase2.py"""
import hmac, hashlib, json, os, sqlite3, sys
from base64 import b64encode

os.environ.setdefault("SLOTKEEPER_DB", "/tmp/slotkeeper-phase2-test.db")
os.environ["OWNER_USER"] = "admin"
os.environ["OWNER_PASSWORD"] = "t123"
os.environ["VERIFY_TOKEN"] = "secret"
os.environ["APP_SECRET"] = "topsecret"
os.environ["SCHEDULER_IN_WEB"] = "0"  # keep the background thread out of tests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.path.exists(os.environ["SLOTKEEPER_DB"]):
    os.remove(os.environ["SLOTKEEPER_DB"])

from app import app  # noqa: E402
import db  # noqa: E402
from bot import handle_message, reset  # noqa: E402
from scheduler import process_due  # noqa: E402

client = app.test_client()
auth = {"Authorization": "Basic " + b64encode(b"admin:t123").decode()}


def convo(user, msgs):
    reset(user)
    return [handle_message(user, m) for m in msgs]


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    print(f"ok - {name}")


r = convo("923001111111", ["book", "1", "tomorrow", "5pm", "Ali", "yes"])
check("booking stored with UTC time", "Booking ref: #1" in r[-1] and db.get_booking(1)["appointment_at"])
r = convo("923002222222", ["book", "2", "tomorrow", "5pm", "Sara", "yes"])
check("double-booking blocked", "just taken" in r[-1] and len(db.list_bookings()) == 1)
r = convo("923001111111", ["cancel", "yes"])
check("cancel flow", "Cancelled" in r[-1] and db.get_booking(1)["status"] == "Cancelled")
check("cancel kills reminders", all(x["status"] == "cancelled" for x in db.list_reminders()))
r = convo("923001111111", ["book", "3", "tomorrow", "6pm", "Ali", "yes"])
bid = db.list_bookings()[0]["id"]
check("cross-customer cancel blocked", db.cancel_booking(bid, "923002222222") is False)
r = convo("923001111111", ["reschedule", "tomorrow", "7pm", "yes"])
check("reschedule flow", "moved to" in r[-1] and db.get_booking(bid)["time"] == "7pm")
convo("923001111111", ["book", "1", "tomorrow", "9pm", "Ali", "yes"])
r = convo("923001111111", ["reschedule", "1", "tomorrow", "7pm", "yes"])
check("reschedule conflict preserves original", "just taken" in r[-1]
      and any(b["time"] == "9pm" for b in db.customer_bookings("923001111111")))

db.set_booking_status(bid, "Confirmed")
c = sqlite3.connect(os.environ["SLOTKEEPER_DB"])
c.execute("UPDATE reminders SET send_at = datetime('now', '-1 minute') WHERE booking_id = ? AND kind = '2h'", (bid,))
c.commit(); c.close()
check("scheduler processes due", process_due() >= 1)
st = {x["kind"]: x["status"] for x in db.list_reminders() if x["booking_id"] == bid}
check("manual mode when API unconfigured", st.get("2h") == "manual", str(st))

db.cancel_booking(bid, "923001111111")
c = sqlite3.connect(os.environ["SLOTKEEPER_DB"])
c.execute("UPDATE reminders SET send_at = datetime('now', '-1 minute'), status = 'pending' WHERE booking_id = ?", (bid,))
c.commit(); c.close()
process_due()
check("scheduler skips cancelled bookings",
      all(x["status"] == "cancelled" for x in db.list_reminders() if x["booking_id"] == bid))

body = json.dumps({"entry": []}).encode()
bad = client.post("/webhook", data=body, content_type="application/json",
                  headers={"X-Hub-Signature-256": "sha256=deadbeef"})
check("bad HMAC rejected", bad.status_code == 403)
good = client.post("/webhook", data=body, content_type="application/json",
                   headers={"X-Hub-Signature-256": "sha256=" + hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()})
check("good HMAC accepted", good.status_code == 200)

bid3 = db.create_booking("923003333333", "Api", "Facial", "PKR 1,500", "tomorrow", "4pm", "2026-10-17T11:00:00+00:00")
r = client.post(f"/api/bookings/{bid3}/reschedule", json={"date": "tomorrow", "time": "6pm"}, headers=auth)
check("dashboard reschedule API", r.status_code == 200, str(r.get_json()))
r = client.get("/api/reminders", headers=auth)
check("reminders API", r.status_code == 200 and isinstance(r.get_json(), list))
r = client.get("/dashboard", headers=auth)
check("dashboard renders", r.status_code == 200 and b"Reminder queue" in r.data)

print("\nALL PHASE-2 TESTS PASSED")
