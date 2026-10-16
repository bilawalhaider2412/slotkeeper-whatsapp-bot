"""Missed-call text-back tests. Run:  cd ~/workspace/bots/slotkeeper && python3 tests/test_missed_call.py"""
import os
import sys
from base64 import b64encode

os.environ.setdefault("SLOTKEEPER_DB", "/tmp/slotkeeper-missedcall-test.db")
os.environ["OWNER_USER"] = "admin"
os.environ["OWNER_PASSWORD"] = "t123"
os.environ["VERIFY_TOKEN"] = "secret"
os.environ["MISSED_CALL_SECRET"] = "notifier-secret"
os.environ["MISSED_CALL_COOLDOWN_MIN"] = "30"
os.environ["SCHEDULER_IN_WEB"] = "0"  # keep the background thread out of tests
# no WHATSAPP_TOKEN -> manual mode unless we monkeypatch send_whatsapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if os.path.exists(os.environ["SLOTKEEPER_DB"]):
    os.remove(os.environ["SLOTKEEPER_DB"])

import app as appmod  # noqa: E402
import db  # noqa: E402
from bot import missed_call_textback  # noqa: E402

client = appmod.app.test_client()
auth = {"Authorization": "Basic " + b64encode(b"admin:t123").decode()}


def check(name, cond, detail=""):
    assert cond, f"FAILED: {name} {detail}"
    print(f"ok - {name}")


# --- message content ---
msg = missed_call_textback()
check("text-back mentions business", "Glow Salon" in msg)
check("text-back invites booking", "book" in msg.lower())

# --- auth: bad secret rejected ---
r = client.post("/api/missed-call", json={"caller_phone": "923001111111", "secret": "wrong"})
check("bad secret rejected", r.status_code == 403)

# --- auth: missing phone rejected ---
r = client.post("/api/missed-call", json={"secret": "notifier-secret"})
check("missing phone rejected", r.status_code == 400)

# --- secret auth works; manual mode (no WHATSAPP_TOKEN) returns draft ---
r = client.post("/api/missed-call", json={"caller_phone": "923001111111", "secret": "notifier-secret"})
d = r.get_json()
check("first missed call accepted", r.status_code == 200 and d["ok"])
check("manual mode returns draft", d["sent"] is False and "Glow Salon" in d["draft"], str(d))
check("logged", len(db.list_missed_calls()) == 1)

# --- owner basic auth also works (no secret needed) ---
r = client.post("/api/missed-call", json={"caller_phone": "923002222222"}, headers=auth)
check("owner auth accepted", r.status_code == 200 and r.get_json()["ok"])

# --- send path: monkeypatch send_whatsapp ---
sent = []
appmod.send_whatsapp = lambda to, text: sent.append((to, text)) or {"sent": True}
r = client.post("/api/missed-call", json={"caller_phone": "923003333333", "secret": "notifier-secret"})
d = r.get_json()
check("send path delivers", d["sent"] is True and sent[-1][0] == "923003333333", str(d))
entry = db.list_missed_calls()[0]
check("sent flag recorded", entry["textback_sent"] == 1 and entry["sent_at"])

# --- cooldown: same caller again within window -> logged but skipped ---
n_before = len(sent)
r = client.post("/api/missed-call", json={"caller_phone": "923003333333", "secret": "notifier-secret"})
d = r.get_json()
check("cooldown skips resend", d["sent"] is False and d.get("reason") == "cooldown"
      and len(sent) == n_before, str(d))

# --- after cooldown expires -> sends again ---
import sqlite3
c = sqlite3.connect(os.environ["SLOTKEEPER_DB"])
c.execute("UPDATE missed_calls SET sent_at = datetime('now', '-2 hours') WHERE caller_phone = '923003333333'")
c.commit(); c.close()
r = client.post("/api/missed-call", json={"caller_phone": "923003333333", "secret": "notifier-secret"})
check("sends again after cooldown", r.get_json()["sent"] is True and len(sent) == n_before + 1)

# --- dashboard list endpoint (owner protected) ---
r = client.get("/api/missed-calls", headers=auth)
check("list endpoint", r.status_code == 200 and len(r.get_json()) >= 4)
r = client.get("/api/missed-calls")
check("list endpoint requires auth", r.status_code == 401)

# --- dashboard renders with new counts key ---
r = client.get("/dashboard", headers=auth)
check("dashboard renders", r.status_code == 200 and b"Missed calls" in r.data)

# --- protection: phone validation ---
for bad in ["123", "abcdef", "", "12"]:
    r = client.post("/api/missed-call",
                    json={"caller_phone": bad, "secret": "notifier-secret"})
    check(f"invalid phone rejected ({bad!r})", r.status_code == 400)

# --- protection: rate limiting ---
appmod._rate_buckets.clear()
appmod.MISSED_CALL_RATE_PER_MIN = 3
codes = []
for i in range(4):
    rr = client.post("/api/missed-call",
                     json={"caller_phone": f"92300999{i:03d}", "secret": "notifier-secret"})
    codes.append(rr.status_code)
check("rate limit kicks in", codes[-1] == 429 and codes[0] == 200, str(codes))
appmod._rate_buckets.clear()
appmod.MISSED_CALL_RATE_PER_MIN = 30

# --- protection: daily cap ---
appmod.MISSED_CALL_DAILY_CAP = 0  # force the cap to trip
r = client.post("/api/missed-call",
                json={"caller_phone": "923008888888", "secret": "notifier-secret"})
d = r.get_json()
check("daily cap blocks sending", d["sent"] is False and d.get("reason") == "daily cap", str(d))
check("cap logged in dashboard",
      any(x["caller_phone"] == "923008888888" and x["note"] == "daily cap reached"
          for x in db.list_missed_calls()))
appmod.MISSED_CALL_DAILY_CAP = 200

print("\nAll missed-call text-back checks passed.")
