"""
SlotKeeper WhatsApp bot - one deployable app.

- POST /webhook  : Meta WhatsApp Cloud API webhook (bot takes bookings)
- GET  /dashboard: owner's dashboard (booking log, confirm/cancel,
                    waiting list, message drafts, CSV export)

Deploy to Render / Railway / any VPS, set the env vars, point Meta's
webhook at https://YOUR-DOMAIN/webhook.
"""

import csv
import hashlib
import hmac
import io
import os
import threading
import time
from functools import wraps

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request

import db
from bot import BUSINESS, confirmation_draft, handle_message, reminder_draft, parse_when, missed_call_textback, is_opt_out_text, is_opt_in_text, opt_out_confirm, opt_in_confirm
from scheduler import POLL_SECONDS, process_due

app = Flask(__name__)
db.init_db()

VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "change-me")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
APP_SECRET = os.environ.get("APP_SECRET", "")  # Meta app secret -> enables HMAC check
GRAPH_API_VERSION = os.environ.get("GRAPH_API_VERSION", "v24.0")
OWNER_USER = os.environ.get("OWNER_USER", "admin")
OWNER_PASSWORD = os.environ.get("OWNER_PASSWORD", "change-me")
# Shared secret for the missed-call notifier (phone app / telephony webhook).
# If unset, the /api/missed-call endpoint falls back to owner basic auth.
MISSED_CALL_SECRET = os.environ.get("MISSED_CALL_SECRET", "")
MISSED_CALL_COOLDOWN_MIN = int(os.environ.get("MISSED_CALL_COOLDOWN_MIN", "30"))
# Abuse protection: max requests/minute per caller IP, and max text-backs
# actually sent per day (bounds WhatsApp cost if the notifier misbehaves).
MISSED_CALL_RATE_PER_MIN = int(os.environ.get("MISSED_CALL_RATE_PER_MIN", "30"))
MISSED_CALL_DAILY_CAP = int(os.environ.get("MISSED_CALL_DAILY_CAP", "200"))

# In-memory sliding-window rate limiter (single gunicorn worker by design,
# same as the embedded scheduler — see render.yaml).
_rate_buckets = {}


def _rate_limited(ip, limit_per_min):
    now = time.time()
    bucket = _rate_buckets.setdefault(ip, [])
    bucket[:] = [t for t in bucket if now - t < 60]
    if len(bucket) >= limit_per_min:
        return True
    bucket.append(now)
    return False


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")


# ---------- WhatsApp Cloud API ----------

def send_whatsapp(to_number, text):
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        return {"sent": False, "reason": "WhatsApp API not configured"}
    url = (f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
           f"{PHONE_NUMBER_ID}/messages")
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}",
                 "Content-Type": "application/json"},
        json={"messaging_product": "whatsapp", "to": to_number,
              "type": "text", "text": {"body": text}},
        timeout=15,
    )
    resp.raise_for_status()
    return {"sent": True}


@app.route("/webhook", methods=["GET"])
def verify():
    if (request.args.get("hub.mode") == "subscribe"
            and request.args.get("hub.verify_token") == VERIFY_TOKEN):
        return request.args.get("hub.challenge"), 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def incoming():
    # HMAC-SHA256 authenticity check (Meta X-Hub-Signature-256). If no
    # APP_SECRET is configured we accept but log it - dev mode only.
    if APP_SECRET:
        sig = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(APP_SECRET.encode(),
                                        request.get_data(),
                                        hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            app.logger.warning("webhook: invalid signature rejected")
            return "invalid signature", 403
    else:
        app.logger.warning("webhook: APP_SECRET not set - accepting without signature check (dev mode)")
    data = request.get_json(silent=True) or {}
    try:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    if message.get("type") != "text":
                        continue
                    sender = message["from"]
                    body = message["text"]["body"]
                    # STOP / opt-out handling runs before the normal bot flow.
                    if is_opt_out_text(body):
                        db.set_opt_out(sender, True, source="whatsapp")
                        send_whatsapp(sender, opt_out_confirm())
                        continue
                    if db.is_opted_out(sender):
                        if is_opt_in_text(body):
                            db.set_opt_out(sender, False)
                            send_whatsapp(sender, opt_in_confirm())
                        # otherwise: stay silent, the user asked for no messages
                        continue
                    reply = handle_message(sender, body)
                    send_whatsapp(sender, reply)
    except Exception as exc:
        app.logger.exception("webhook error: %s", exc)
    return "ok", 200


# ---------- Owner dashboard ----------

def _protected(view):
    @wraps(view)
    def wrapper(*a, **kw):
        auth = request.authorization
        if not auth or auth.username != OWNER_USER or auth.password != OWNER_PASSWORD:
            return Response("Owner login required", 401,
                            {"WWW-Authenticate": 'Basic realm="SlotKeeper"'})
        return view(*a, **kw)
    return wrapper


@app.route("/")
def index():
    return redirect("/dashboard")


@app.route("/dashboard")
@_protected
def dashboard():
    try:
        return render_template("dashboard.html", business=BUSINESS,
                               counts=db.counts(), scheduler=scheduler_status())
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return (f"<h1>Dashboard failed to load</h1>"
                f"<p><b>{type(exc).__name__}:</b> {exc}</p>"
                f"<p>Open <a href='/debug'>/debug</a> for details.</p>"), 500


@app.route("/debug")
@_protected
def debug():
    """Temporary diagnostic page: shows exactly what the server sees."""
    import pathlib
    root = pathlib.Path(".")
    info = {
        "cwd": str(root.resolve()),
        "root_files": sorted(p.name for p in root.iterdir()),
        "templates": sorted(p.name for p in (root / "templates").iterdir()) if (root / "templates").exists() else "MISSING - templates folder not found!",
        "env_set": {k: bool(os.environ.get(k)) for k in
                    ["VERIFY_TOKEN", "WHATSAPP_TOKEN", "PHONE_NUMBER_ID", "APP_SECRET", "OWNER_USER"]},
    }
    try:
        info["db_counts"] = db.counts()
    except Exception as exc:
        info["db_counts"] = f"ERROR: {type(exc).__name__}: {exc}"
    try:
        render_template("dashboard.html", business=BUSINESS, counts=db.counts(),
                        scheduler=scheduler_status())
        info["template_renders"] = True
    except Exception as exc:
        info["template_renders"] = f"ERROR: {type(exc).__name__}: {exc}"
    rows = "".join(f"<tr><td><b>{k}</b></td><td><pre>{v}</pre></td></tr>" for k, v in info.items())
    return f"<h1>SlotKeeper debug</h1><table border='1' cellpadding='8'>{rows}</table>"


@app.route("/api/scheduler")
@_protected
def api_scheduler():
    return jsonify(scheduler_status())


@app.route("/api/bookings")
@_protected
def api_bookings():
    return jsonify(db.list_bookings(request.args.get("status") or None))


@app.route("/api/bookings/<int:booking_id>/status", methods=["POST"])
@_protected
def api_set_status(booking_id):
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("Awaiting confirmation", "Confirmed", "Cancelled"):
        return jsonify({"error": "bad status"}), 400
    db.set_booking_status(booking_id, status)
    return jsonify({"ok": True})


@app.route("/api/bookings/<int:booking_id>/notify", methods=["POST"])
@_protected
def api_notify(booking_id):
    kind = (request.get_json(silent=True) or {}).get("kind", "confirmation")
    booking = db.get_booking(booking_id)
    if not booking:
        return jsonify({"error": "not found"}), 404
    if db.is_opted_out(booking["phone"]):
        return jsonify({"error": "customer opted out (STOP) — re-allow them from the dashboard before messaging"}), 409
    text = confirmation_draft(booking) if kind == "confirmation" else reminder_draft(booking)
    result = send_whatsapp(booking["phone"], text)
    if result.get("sent"):
        return jsonify({"sent": True})
    # Manual mode: hand the owner the ready-to-send text
    return jsonify({"sent": False, "draft": text})


@app.route("/api/bookings/<int:booking_id>/reschedule", methods=["POST"])
@_protected
def api_reschedule(booking_id):
    data = request.get_json(silent=True) or {}
    booking = db.get_booking(booking_id)
    if not booking:
        return jsonify({"error": "not found"}), 404
    local_dt, utc_iso = parse_when(data.get("date", ""), data.get("time", ""))
    if not local_dt:
        return jsonify({"error": "could not understand that date/time"}), 400
    ok, reason = db.reschedule_booking(booking_id, booking["phone"],
                                       data.get("date", ""), data.get("time", ""),
                                       utc_iso)
    if not ok:
        return jsonify({"error": "slot taken" if reason == "conflict" else reason}), 409
    return jsonify({"ok": True, "when": local_dt.strftime("%A, %d %b at %I:%M %p")})


@app.route("/api/reminders")
@_protected
def api_reminders():
    return jsonify(db.list_reminders())


@app.route("/api/waiting", methods=["GET", "POST"])
@_protected
def api_waiting():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        entry_id = db.add_waiting(data.get("phone", ""), data.get("name", ""),
                                 data.get("note", ""))
        return jsonify({"ok": True, "id": entry_id})
    return jsonify(db.list_waiting())


@app.route("/api/waiting/<int:entry_id>", methods=["DELETE"])
@_protected
def api_waiting_delete(entry_id):
    db.delete_waiting(entry_id)
    return jsonify({"ok": True})


# ---------- missed-call text-back ----------
# Called by the missed-call notifier (phone app / telephony webhook) the
# moment a call goes unanswered. Auth: shared MISSED_CALL_SECRET in the
# JSON body, or owner basic auth when no secret is configured.

def _missed_call_authorized(data):
    auth = request.authorization
    if auth and auth.username == OWNER_USER and auth.password == OWNER_PASSWORD:
        return True
    if MISSED_CALL_SECRET:
        # timing-safe compare: never leak secret validity via response time
        return hmac.compare_digest(str(data.get("secret", "")), MISSED_CALL_SECRET)
    return False


@app.route("/api/missed-call", methods=["POST"])
def api_missed_call():
    data = request.get_json(silent=True) or {}
    if not _missed_call_authorized(data):
        return jsonify({"error": "unauthorized"}), 403
    if _rate_limited(_client_ip(), MISSED_CALL_RATE_PER_MIN):
        return jsonify({"error": "rate limited, slow down"}), 429
    caller = "".join(ch for ch in str(data.get("caller_phone", "")) if ch.isdigit()).strip()
    # Plausible phone numbers only: 7-15 digits (E.164). Rejects junk that
    # would burn WhatsApp quota or error out at Meta.
    if not (7 <= len(caller) <= 15):
        return jsonify({"error": "caller_phone must be 7-15 digits"}), 400
    if db.textbacks_sent_today() >= MISSED_CALL_DAILY_CAP:
        entry, _ = db.log_missed_call(caller, MISSED_CALL_COOLDOWN_MIN)
        db.mark_textback(entry["id"], False, "daily cap reached")
        app.logger.warning("missed-call: daily cap reached, not sending to %s", caller)
        return jsonify({"ok": True, "sent": False, "reason": "daily cap"})
    if db.is_opted_out(caller):
        entry, _ = db.log_missed_call(caller, MISSED_CALL_COOLDOWN_MIN)
        db.mark_textback(entry["id"], False, "opted out: no messages per user request")
        return jsonify({"ok": True, "sent": False, "reason": "opted out"})
    entry, should_send = db.log_missed_call(caller, MISSED_CALL_COOLDOWN_MIN)
    if not should_send:
        db.mark_textback(entry["id"], False, "cooldown: text-back already sent recently")
        return jsonify({"ok": True, "sent": False, "reason": "cooldown"})
    text = missed_call_textback()
    result = send_whatsapp(caller, text)
    if result.get("sent"):
        db.mark_textback(entry["id"], True)
        return jsonify({"ok": True, "sent": True, "id": entry["id"]})
    # Manual mode: owner forwards the ready text themselves
    db.mark_textback(entry["id"], False, "manual: WhatsApp API not configured")
    return jsonify({"ok": True, "sent": False, "draft": text, "id": entry["id"]})


@app.route("/api/missed-calls")
@_protected
def api_missed_calls():
    return jsonify(db.list_missed_calls())


@app.route("/api/opt-outs")
@_protected
def api_opt_outs():
    return jsonify(db.list_opt_outs())


@app.route("/api/opt-outs/clear", methods=["POST"])
@_protected
def api_opt_out_clear():
    phone = "".join(ch for ch in str((request.get_json(silent=True) or {}).get("phone", "")) if ch.isdigit())
    if not phone:
        return jsonify({"error": "phone required"}), 400
    db.set_opt_out(phone, False)
    return jsonify({"ok": True})


@app.route("/export.csv")
@_protected
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "phone", "name", "service", "price", "date", "time", "status", "created_at"])
    for b in db.list_bookings():
        w.writerow([b["id"], b["phone"], b["name"], b["service"], b["price"],
                    b["date"], b["time"], b["status"], b["created_at"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=bookings.csv"})


@app.route("/health")
def health():
    return "slotkeeper running", 200


# ---------- embedded scheduler ----------
# Reminders run in a background thread inside the web process: one deploy,
# no separate worker. Keep gunicorn to a single worker (see render.yaml)
# so jobs are never processed twice.
_scheduler_state = {"last_run": None, "last_error": None}


def _scheduler_loop():
    while True:
        try:
            process_due()
            _scheduler_state["last_run"] = time.time()
            _scheduler_state["last_error"] = None
        except Exception as exc:  # never kill the web app over reminders
            _scheduler_state["last_error"] = str(exc)
            app.logger.exception("scheduler error: %s", exc)
        time.sleep(POLL_SECONDS)


def scheduler_status():
    last = _scheduler_state["last_run"]
    return {
        "running": True,
        "poll_seconds": POLL_SECONDS,
        "last_run_ago_s": round(time.time() - last) if last else None,
        "last_error": _scheduler_state["last_error"],
    }


if os.environ.get("SCHEDULER_IN_WEB", "1") == "1":
    threading.Thread(target=_scheduler_loop, daemon=True,
                     name="slotkeeper-scheduler").start()
    app.logger.info("embedded scheduler started (every %ss)", POLL_SECONDS)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
