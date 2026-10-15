"""Terminal demo: bot takes a booking, dashboard shows it. No Meta account needed."""

import os
import sys

os.environ["SLOTKEEPER_DB"] = "/tmp/slotkeeper-demo.db"
if os.path.exists("/tmp/slotkeeper-demo.db"):
    os.remove("/tmp/slotkeeper-demo.db")

import db
from bot import handle_message, reset

db.init_db()

SCRIPT = ["hi", "book", "2", "Friday", "5pm", "Bilawal", "yes"]

user = "923001234567"
reset(user)
for msg in SCRIPT:
    print(f"You: {msg}")
    print(f"Bot: {handle_message(user, msg)}")
    print("-" * 50)

print("\nBookings now in the database (what the owner sees on the dashboard):")
for b in db.list_bookings():
    print(f"  #{b['id']} {b['name']} ({b['phone']}) — {b['service']} {b['price']}, "
          f"{b['date']} {b['time']} — {b['status']}")

print("\nStart the dashboard with:  cd ~/workspace/bots/slotkeeper && python3 app.py")
print("Then open http://localhost:5000/dashboard  (login: admin / change-me)")
