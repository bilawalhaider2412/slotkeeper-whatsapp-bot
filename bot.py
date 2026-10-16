"""
SlotKeeper WhatsApp bot - conversation logic (Phase 2).

Booking, cancellation, and rescheduling over WhatsApp, with real
datetime parsing (Asia/Karachi), slot-conflict protection, and
phone-verified identity: a customer can only touch their own bookings.

Keyword coverage includes English + Roman Urdu. Full Urdu-script NLU
is a later phase; unknown input falls back to a human handoff.
"""

from datetime import timezone

import dateparser

import db
from db import SlotConflict

BUSINESS = {
    "name": "Glow Salon",
    "city": "Peshawar",
    "hours": "Mon-Sat, 10am - 8pm (closed Sundays)",
    "address": "Shop 4, Main University Road, Peshawar",
    "timezone": "Asia/Karachi",
    "services": {
        "1": ("Haircut", "PKR 500"),
        "2": ("Haircut + Beard", "PKR 800"),
        "3": ("Facial", "PKR 1,500"),
        "4": ("Hair Colour", "PKR 2,500"),
    },
}

_sessions = {}


def _session(user_id):
    return _sessions.setdefault(user_id, {"step": "idle", "data": {}})


def reset(user_id):
    _sessions.pop(user_id, None)


def parse_when(date_text, time_text):
    """'Friday' + '5pm' -> (local datetime, UTC ISO string) or (None, None)."""
    dt = dateparser.parse(
        f"{date_text} {time_text}",
        settings={
            "TIMEZONE": BUSINESS["timezone"],
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
        },
    )
    if not dt:
        return None, None
    return dt, dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _services_text():
    lines = [f"{k}. {v[0]} - {v[1]}" for k, v in BUSINESS["services"].items()]
    return "\n".join(lines)


def _greeting():
    return (
        f"Assalam-o-Alaikum! Welcome to {BUSINESS['name']} ({BUSINESS['city']}).\n\n"
        "I can help you with:\n"
        "• Services & prices (reply: services)\n"
        "• Book an appointment (reply: book)\n"
        "• Cancel a booking (reply: cancel)\n"
        "• Reschedule (reply: reschedule)\n"
        "• Timings & location (reply: hours)\n"
        "• Talk to the owner (reply: human)"
    )


def _booking_line(b):
    return f"#{b['id']}: {b['service']} — {b['date']} at {b['time']} ({b['status']})"


def _pick_booking_text(bookings, action):
    lines = [f"{i+1}. {_booking_line(b)}" for i, b in enumerate(bookings)]
    return (f"Which booking do you want to {action}?\n\n" + "\n".join(lines)
            + "\n\nReply with the number.")


def _find_pick(bookings, low):
    if low.isdigit():
        i = int(low) - 1
        if 0 <= i < len(bookings):
            return bookings[i]
    for b in bookings:
        if str(b["id"]) == low.lstrip("#"):
            return bookings[[x["id"] for x in bookings].index(b["id"])] if False else b
    return None


def handle_message(user_id, text):
    text = (text or "").strip()
    low = text.lower()
    s = _session(user_id)
    step = s["step"]
    d = s["data"]

    # ---- global commands ----
    if low in ("cancel", "stop", "mansookh"):
        # "cancel" alone at idle = cancel a booking; mid-flow = abort flow
        if step == "idle" and low == "cancel":
            pass  # fall through to booking-cancel flow below
        else:
            reset(user_id)
            return "No problem - I've cancelled that. Reply 'hi' any time to start over."
    if low in ("hi", "hello", "salam", "assalam", "assalam-o-alaikum", "assalamu alaikum",
               "start", "menu"):
        reset(user_id)
        return _greeting()
    if low in ("human", "owner", "call", "agent", "insan", "bandah", "admi"):
        reset(user_id)
        return ("Of course - I've noted you want to talk to a person. "
                "The owner will reply to you here shortly. Thank you!")

    # ---- booking flow ----
    if step == "idle" and low in ("book", "booking", "appointment", "2", "book karna hai"):
        s["step"] = "service"
        return ("Great! Which service would you like to book?\n\n"
                f"{_services_text()}\n\nReply with the number (1-4).")

    if step == "service":
        if low in BUSINESS["services"]:
            d["service"] = BUSINESS["services"][low]
            s["step"] = "date"
            return (f"{d['service'][0]} ({d['service'][1]}) - good choice!\n"
                    "Which day? (e.g. tomorrow, Friday, 20 Oct)")
        return f"Please reply with a number 1-{len(BUSINESS['services'])}.\n\n{_services_text()}"

    if step == "date":
        d["date"] = text
        s["step"] = "time"
        return f"Noted: {text}.\nWhat time works for you? (e.g. 5pm, 11am)"

    if step == "time":
        d["time"] = text
        local_dt, utc_iso = parse_when(d["date"], d["time"])
        if not local_dt:
            s["step"] = "date"
            return ("Sorry, I didn't understand that date/time. Let's try again — "
                    "which day? (e.g. tomorrow, Friday, 20 Oct)")
        d["appointment_at"] = utc_iso
        d["when_local"] = local_dt.strftime("%A, %d %b at %I:%M %p")
        s["step"] = "name"
        return f"Got it: {d['when_local']}.\nFinally, your name please?"

    if step == "name":
        d["name"] = text
        s["step"] = "confirm"
        svc, price = d["service"]
        return ("Please confirm your booking:\n\n"
                f"Service: {svc} ({price})\n"
                f"When: {d['when_local']}\n"
                f"Name: {d['name']}\n\n"
                "Reply YES to confirm.")

    if step == "confirm":
        if low in ("yes", "y", "confirm", "han", "haa", "jee", "ji"):
            svc, price = d["service"]
            try:
                booking_id = db.create_booking(user_id, d["name"], svc, price,
                                               d["date"], d["time"], d["appointment_at"])
            except SlotConflict:
                s["step"] = "date"
                return ("That time was just taken by another customer. "
                        "Please pick another day — which day works? (e.g. Saturday, 21 Oct)")
            reset(user_id)
            return ("Done! Your booking request is in:\n\n"
                    f"{svc} ({price}) — {d['when_local']} under {d['name']}.\n"
                    f"Booking ref: #{booking_id}\n\n"
                    f"{BUSINESS['name']} will confirm shortly. "
                    "Reply 'hi' for anything else, or 'cancel' / 'reschedule' to change it.")
        reset(user_id)
        return "Booking not confirmed - cancelled. Reply 'book' to try again."

    # ---- cancel flow ----
    if step == "idle" and low in ("cancel", "mansookh", "cancel karna hai"):
        bookings = db.customer_bookings(user_id)
        if not bookings:
            return "You have no active bookings to cancel. Reply 'book' to make one."
        if len(bookings) == 1:
            d["target"] = bookings[0]
            s["step"] = "cancel_confirm"
            return (f"Cancel this booking?\n\n{_booking_line(bookings[0])}\n\n"
                    "Reply YES to cancel it.")
        d["bookings"] = bookings
        s["step"] = "cancel_which"
        return _pick_booking_text(bookings, "cancel")

    if step == "cancel_which":
        b = _find_pick(d["bookings"], low)
        if not b:
            return "Please reply with one of the numbers listed."
        d["target"] = b
        s["step"] = "cancel_confirm"
        return f"Cancel this booking?\n\n{_booking_line(b)}\n\nReply YES to cancel it."

    if step == "cancel_confirm":
        if low in ("yes", "y", "han", "jee", "confirm"):
            b = d["target"]
            ok = db.cancel_booking(b["id"], user_id)
            reset(user_id)
            if ok:
                return (f"Cancelled: {_booking_line(b)}.\n\n"
                        "Sorry to see it go — reply 'book' any time to make a new one.")
            return "Something went wrong — that booking may already be cancelled. Reply 'hi' to start over."
        reset(user_id)
        return "Kept your booking as it is. Reply 'hi' for anything else."

    # ---- reschedule flow ----
    if step == "idle" and low in ("reschedule", "tabdeel", "change", "dobara", "time change"):
        bookings = db.customer_bookings(user_id)
        if not bookings:
            return "You have no active bookings to reschedule. Reply 'book' to make one."
        if len(bookings) == 1:
            d["target"] = bookings[0]
            s["step"] = "resched_date"
            return (f"Rescheduling {_booking_line(bookings[0])}.\n\n"
                    "Which new day? (e.g. tomorrow, Friday, 20 Oct)")
        d["bookings"] = bookings
        s["step"] = "resched_which"
        return _pick_booking_text(bookings, "reschedule")

    if step == "resched_which":
        b = _find_pick(d["bookings"], low)
        if not b:
            return "Please reply with one of the numbers listed."
        d["target"] = b
        s["step"] = "resched_date"
        return f"Rescheduling {_booking_line(b)}.\n\nWhich new day? (e.g. tomorrow, Friday, 20 Oct)"

    if step == "resched_date":
        d["new_date"] = text
        s["step"] = "resched_time"
        return "And what new time? (e.g. 5pm, 11am)"

    if step == "resched_time":
        local_dt, utc_iso = parse_when(d["new_date"], text)
        if not local_dt:
            s["step"] = "resched_date"
            return ("Sorry, I didn't understand that. Which new day? "
                    "(e.g. tomorrow, Friday, 20 Oct)")
        d["new_time"] = text
        d["new_at"] = utc_iso
        d["new_when_local"] = local_dt.strftime("%A, %d %b at %I:%M %p")
        s["step"] = "resched_confirm"
        b = d["target"]
        return (f"Move booking #{b['id']} ({b['service']}) to:\n\n"
                f"{d['new_when_local']}\n\nReply YES to confirm the change.")

    if step == "resched_confirm":
        if low in ("yes", "y", "han", "jee", "confirm"):
            b = d["target"]
            ok, reason = db.reschedule_booking(b["id"], user_id, d["new_date"],
                                               d["new_time"], d["new_at"])
            reset(user_id)
            if ok:
                return (f"Done! Booking #{b['id']} moved to {d['new_when_local']}.\n\n"
                        "Reply 'hi' for anything else.")
            if reason == "conflict":
                return ("That new time was just taken. Reply 'reschedule' to try another time.")
            return "Something went wrong — reply 'hi' to start over."
        reset(user_id)
        return "Kept your booking as it is. Reply 'hi' for anything else."

    # ---- FAQ intents (idle) ----
    if any(w in low for w in ("service", "price", "rate", "menu", "charges", "1", "kharcha", "paisay")):
        return (f"Here are our services at {BUSINESS['name']}:\n\n"
                f"{_services_text()}\n\nReply 'book' to make an appointment.")
    if any(w in low for w in ("hour", "timing", "time", "open", "close", "location", "address",
                              "where", "3", "kab khula", "pata")):
        return (f"{BUSINESS['name']} - {BUSINESS['city']}\n\n"
                f"Hours: {BUSINESS['hours']}\n"
                f"Address: {BUSINESS['address']}\n\n"
                "Reply 'book' to make an appointment.")
    if "my booking" in low or "meri booking" in low or low in ("status", "bookings"):
        bookings = db.customer_bookings(user_id)
        if not bookings:
            return "You have no active bookings. Reply 'book' to make one."
        return "Your active bookings:\n\n" + "\n".join(_booking_line(b) for b in bookings)

    return ("Sorry, I didn't quite get that.\n\n"
            "You can reply: services, book, cancel, reschedule, hours, "
            "or human to talk to the owner.")


def confirmation_draft(booking):
    return (
        f"Assalam-o-Alaikum {booking['name']}! Your appointment at {BUSINESS['name']} "
        f"is CONFIRMED:\n\n{booking['service']} ({booking['price']})\n"
        f"{booking['date']} at {booking['time']}\nBooking ref: #{booking['id']}\n\n"
        f"See you then!"
    )


def reminder_draft(booking):
    return (
        f"Assalam-o-Alaikum {booking['name']}, friendly reminder of your appointment "
        f"at {BUSINESS['name']}:\n\n{booking['service']} — {booking['date']} at {booking['time']}\n"
        f"Booking ref: #{booking['id']}\n\nReply here if you need to reschedule."
    )


def missed_call_textback():
    """Instant WhatsApp reply sent to a missed caller."""
    return (
        f"Assalam-o-Alaikum! Sorry we missed your call \U0001f649\n"
        f"This is the assistant for {BUSINESS['name']} ({BUSINESS['city']}). "
        f"Want to book an appointment?\n\n"
        f"Reply: 1 - Book  |  2 - Timings  |  3 - Prices"
    )


# ---------- opt-out (STOP) handling ----------

OPT_OUT_PHRASES = (
    "stop", "unsubscribe", "opt out", "optout", "stop messages",
    "cancel messages", "no more messages", "do not message",
    "bas", "bas karo", "band karo", "band kar do", "nahi chahiye",
    "stop karo", "message mat karo", "message na karo", "msg mat karo",
)
OPT_IN_PHRASES = ("start", "resume", "shuru karo", "opt in")


def _norm(text):
    return " ".join(str(text or "").lower().split())


def is_opt_out_text(text):
    t = _norm(text)
    return any(p == t or f" {p} " in f" {t} " for p in OPT_OUT_PHRASES)


def is_opt_in_text(text):
    t = _norm(text)
    return any(p == t or f" {p} " in f" {t} " for p in OPT_IN_PHRASES)


def opt_out_confirm():
    return (
        "Done — you won't receive messages from us again. "
        "Reply START any time if you'd like to hear from us."
    )


def opt_in_confirm():
    return (
        f"Welcome back! You're opted in again. "
        f"How can {BUSINESS['name']} help you today? Reply 1 to book."
    )
