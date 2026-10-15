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
from bot import BUSINESS, confirmation_draft, handle_message, reminder_draft, parse_when
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
                    reply = handle_message(sender, message["text"]["body"])
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
    return render_template("dashboard.html", business=BUSINESS,
                           counts=db.counts(), scheduler=scheduler_status())


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
