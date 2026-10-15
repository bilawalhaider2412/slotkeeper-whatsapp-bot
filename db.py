"""
SlotKeeper WhatsApp bot - database layer (SQLite, zero extra dependencies).

Phase 2: bookings carry a real UTC appointment time, reminders are
durable jobs, cancel/reschedule are atomic and phone-verified.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("SLOTKEEPER_DB", os.path.join(os.path.dirname(__file__), "slotkeeper.db"))

ACTIVE_STATUSES = ("Awaiting confirmation", "Confirmed")
REMINDER_LEAD_TIMES = [timedelta(hours=24), timedelta(hours=2)]


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                name TEXT NOT NULL,
                service TEXT NOT NULL,
                price TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                appointment_at TEXT,
                status TEXT NOT NULL DEFAULT 'Awaiting confirmation',
                created_at TEXT NOT NULL
            )"""
        )
        # migrate older DBs
        cols = [r["name"] for r in c.execute("PRAGMA table_info(bookings)").fetchall()]
        if "appointment_at" not in cols:
            c.execute("ALTER TABLE bookings ADD COLUMN appointment_at TEXT")
        c.execute(
            """CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id INTEGER NOT NULL REFERENCES bookings(id),
                kind TEXT NOT NULL,
                send_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                sent_at TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS waiting_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                name TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )


def _now_utc():
    return datetime.now(timezone.utc)


# ---------- bookings ----------

def create_booking(phone, name, service, price, date, time, appointment_at_utc):
    """appointment_at_utc: ISO string. Raises SlotConflict when the slot is taken."""
    if find_conflict(appointment_at_utc):
        raise SlotConflict("That time was just taken — please pick another.")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO bookings (phone, name, service, price, date, time, appointment_at, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'Awaiting confirmation', ?)",
            (phone, name, service, price, date, time, appointment_at_utc,
             _now_utc().isoformat(timespec="seconds")),
        )
        booking_id = cur.lastrowid
    schedule_reminders(booking_id)
    return booking_id


class SlotConflict(Exception):
    pass


def find_conflict(appointment_at_utc, exclude_id=None, window_minutes=30):
    """True if an active booking overlaps the slot (simple fixed-window guard)."""
    target = _parse_dt(appointment_at_utc)
    if not target:
        return False
    with _conn() as c:
        rows = c.execute(
            "SELECT id, appointment_at FROM bookings WHERE status IN ('Awaiting confirmation','Confirmed')"
            " AND appointment_at IS NOT NULL" + (" AND id != ?" if exclude_id else ""),
            (exclude_id,) if exclude_id else (),
        ).fetchall()
    for r in rows:
        other = _parse_dt(r["appointment_at"])
        if not other:
            continue
        if abs((other - target).total_seconds()) < window_minutes * 60:
            return True
    return False


def list_bookings(status=None):
    q = "SELECT * FROM bookings"
    args = ()
    if status:
        q += " WHERE status = ?"
        args = (status,)
    q += " ORDER BY id DESC"
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def customer_bookings(phone, active_only=True):
    q = "SELECT * FROM bookings WHERE phone = ?"
    args = [phone]
    if active_only:
        q += " AND status IN ('Awaiting confirmation','Confirmed')"
    q += " ORDER BY id DESC"
    with _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def get_booking(booking_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return dict(r) if r else None


def set_booking_status(booking_id, status):
    with _conn() as c:
        c.execute("UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id))


def cancel_booking(booking_id, phone):
    """Phone-verified cancel. Releases the slot and kills pending reminders."""
    b = get_booking(booking_id)
    if not b or b["phone"] != phone:
        return False
    if b["status"] not in ACTIVE_STATUSES:
        return False
    with _conn() as c:
        c.execute("UPDATE bookings SET status = 'Cancelled' WHERE id = ?", (booking_id,))
        c.execute("UPDATE reminders SET status = 'cancelled' WHERE booking_id = ? AND status = 'pending'",
                  (booking_id,))
    return True


def reschedule_booking(booking_id, phone, new_date, new_time, new_at_utc):
    """Phone-verified atomic reschedule: new slot claimed only if free, old released."""
    b = get_booking(booking_id)
    if not b or b["phone"] != phone:
        return (False, "not yours")
    if b["status"] not in ACTIVE_STATUSES:
        return (False, "not active")
    if find_conflict(new_at_utc, exclude_id=booking_id):
        return (False, "conflict")
    with _conn() as c:
        c.execute(
            "UPDATE bookings SET date = ?, time = ?, appointment_at = ? WHERE id = ?",
            (new_date, new_time, new_at_utc, booking_id),
        )
        c.execute("UPDATE reminders SET status = 'cancelled' WHERE booking_id = ? AND status = 'pending'",
                  (booking_id,))
    schedule_reminders(booking_id)
    return (True, "ok")


def counts():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        awaiting = c.execute(
            "SELECT COUNT(*) FROM bookings WHERE status = 'Awaiting confirmation'").fetchone()[0]
        waiting = c.execute("SELECT COUNT(*) FROM waiting_list").fetchone()[0]
        due = c.execute(
            "SELECT COUNT(*) FROM reminders WHERE status = 'pending'").fetchone()[0]
    return {"total": total, "awaiting": awaiting, "waiting": waiting, "due_reminders": due}


# ---------- reminders ----------

def schedule_reminders(booking_id):
    """Create 24h/2h reminder jobs. Skips ones that would fire too close to the appointment."""
    b = get_booking(booking_id)
    if not b or not b.get("appointment_at"):
        return []
    try:
        appt = datetime.fromisoformat(b["appointment_at"])
    except ValueError:
        return []
    now = _now_utc()
    created = []
    with _conn() as c:
        for lead in REMINDER_LEAD_TIMES:
            # don't bunch reminders for short-notice bookings
            if appt - now < lead + timedelta(minutes=30):
                continue
            kind = f"{int(lead.total_seconds() // 3600)}h"
            exists = c.execute(
                "SELECT 1 FROM reminders WHERE booking_id = ? AND kind = ? AND status = 'pending'",
                (booking_id, kind)).fetchone()
            if exists:
                continue
            send_at = (appt - lead).isoformat(timespec="seconds")
            cur = c.execute(
                "INSERT INTO reminders (booking_id, kind, send_at, status, created_at)"
                " VALUES (?, ?, ?, 'pending', ?)",
                (booking_id, kind, send_at, now.isoformat(timespec="seconds")),
            )
            created.append(cur.lastrowid)
    return created


def _parse_dt(s):
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def due_reminders(now_utc=None, stale_after_minutes=60):
    """Pending reminders whose time has come. Ancient ones expire instead of piling up."""
    now = now_utc or _now_utc()
    with _conn() as c:
        rows = c.execute(
            """SELECT r.*, b.phone, b.name, b.service, b.price, b.date, b.time,
                      b.status AS booking_status, b.appointment_at
               FROM reminders r JOIN bookings b ON b.id = r.booking_id
               WHERE r.status = 'pending'
               ORDER BY r.send_at""").fetchall()
        due, stale_ids = [], []
        for r in rows:
            send_at = _parse_dt(r["send_at"])
            if not send_at:
                stale_ids.append(r["id"])
                continue
            if send_at < now - timedelta(minutes=stale_after_minutes):
                stale_ids.append(r["id"])
            elif send_at <= now:
                due.append(dict(r))
        if stale_ids:
            c.execute(
                f"UPDATE reminders SET status = 'expired' WHERE id IN ({','.join('?' * len(stale_ids))})",
                stale_ids)
        return due


def mark_reminder(reminder_id, status, note=""):
    with _conn() as c:
        c.execute(
            "UPDATE reminders SET status = ?, note = ?, sent_at = ? WHERE id = ?",
            (status, note, _now_utc().isoformat(timespec="seconds"), reminder_id))


def list_reminders(limit=50):
    with _conn() as c:
        rows = c.execute(
            """SELECT r.*, b.name AS customer, b.service
               FROM reminders r JOIN bookings b ON b.id = r.booking_id
               ORDER BY r.send_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ---------- waiting list ----------

def add_waiting(phone, name, note=""):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO waiting_list (phone, name, note, created_at) VALUES (?, ?, ?, ?)",
            (phone, name, note, _now_utc().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def list_waiting():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM waiting_list ORDER BY id DESC").fetchall()]


def delete_waiting(entry_id):
    with _conn() as c:
        c.execute("DELETE FROM waiting_list WHERE id = ?", (entry_id,))
