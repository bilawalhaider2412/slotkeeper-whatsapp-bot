# SlotKeeper — A-to-Z Deployment Runbook

Everything is built and tested. Two things need YOU (your identity):
a hosting account and the Meta/WhatsApp setup. This guide covers every
click in order. Nothing is skipped.

**Time needed:** ~45–60 minutes the first time, ~15 for the next client.

---

## PART 1 — Put the code on GitHub (10 min)

Render deploys from a GitHub repo. The code is ready at
`~/workspace/bots/slotkeeper/` (already a git repo, committed).

1. Go to **github.com** → sign in → **New repository**
   - Name: `slotkeeper-whatsapp-bot` — Private or Public, your call.
   - Do NOT tick "Add a README".
2. On your computer (where the code is), run:
   ```bash
   cd ~/workspace/bots/slotkeeper
   git remote add origin https://github.com/YOUR-USERNAME/slotkeeper-whatsapp-bot.git
   git push -u origin master
   ```
   (GitHub will ask you to sign in — use a Personal Access Token as the
   password: GitHub → Settings → Developer settings → Personal access
   tokens → Generate.)

---

## PART 2 — Deploy to Render (10 min)

1. Go to **render.com** → sign up (email is fine) → Dashboard.
2. **New +** → **Web Service** → **Connect GitHub** → authorize →
   select `slotkeeper-whatsapp-bot`.
3. Render detects `render.yaml` automatically. Keep the defaults.
4. Before creating, open **Advanced** → add these **Environment Variables**:

   | Key | Value — what to put |
   |---|---|
   | `VERIFY_TOKEN` | Make one up, e.g. `glow-salon-verify-99` (you'll paste it into Meta next) |
   | `APP_SECRET` | From Meta (Part 3, step 6) — add after, or now if you have it |
   | `WHATSAPP_TOKEN` | From Meta (Part 3, step 5) — leave empty for now |
   | `PHONE_NUMBER_ID` | From Meta (Part 3, step 5) — leave empty for now |
   | `OWNER_USER` | `admin` (or the client's name) |
   | `OWNER_PASSWORD` | A strong password — this guards the client dashboard |

5. Click **Create Web Service**. Wait ~3–5 min for the build.
6. When it's live, copy your URL: `https://slotkeeper-whatsapp-bot.onrender.com`
7. Open `https://YOUR-URL/health` → you should see `slotkeeper running`.
8. Open `https://YOUR-URL/dashboard` → log in with OWNER_USER/PASSWORD.

> Render free tier sleeps after 15 min idle (first reply is slow to wake).
> For a paying client, upgrade to Starter ($7/mo) and fold it into the retainer.

---

## PART 3 — Connect WhatsApp via Meta (20 min)

Do this on YOUR Facebook account (or the client's, for their number).

1. Go to **developers.facebook.com** → **My Apps** → **Create App**.
   - Type: **Business**. Name it e.g. `SlotKeeper Client Bots`.
2. In the app dashboard → **Add Product** → **WhatsApp** → Set up.
3. You'll land on **API Setup**. Meta gives you a **free test number**.
   - Note the **Phone number ID** → paste into Render env `PHONE_NUMBER_ID`.
   - Click to generate a **temporary access token** → paste into Render env
     `WHATSAPP_TOKEN`. (For production later: create a permanent
     system-user token — see "Going live" below.)
   - Note the **App Secret**: App Settings → Basic → **App Secret** →
     paste into Render env `APP_SECRET`. (Click Show, enter FB password.)
4. After changing env vars, Render **redeploys automatically** (~2 min).
5. Back in Meta → **Configuration** → **Webhook**:
   - **Callback URL:** `https://YOUR-RENDER-URL/webhook`
   - **Verify token:** exactly what you set as `VERIFY_TOKEN` in Render.
   - Click **Verify and Save** → Meta calls your app; you should see a
     green checkmark. (If it fails: check the URL is https, the token
     matches exactly, and the Render service is awake — open /health first.)
   - Click **Manage** next to webhook fields → **Subscribe** to `messages`.
6. **Test it:** from your personal phone, send `hi` to the Meta test
   number (shown in API Setup). The bot should reply with the salon greeting.
   Book something → open your dashboard → the booking is there.
   That's the full loop verified.

---

## PART 4 — Going live for a paying client

1. In Meta → WhatsApp → API Setup → **add the client's real business
   number** (they'll get an SMS/call code to verify it).
2. Generate a **permanent token**: Business Settings → System Users →
   create user → assign the app → generate token with
   `whatsapp_business_messaging` permission. Replace WHATSAPP_TOKEN.
3. Meta requires **business verification** for production numbers and
   **approved message templates** for proactive reminders. The test number
   skips this — that's why demos start there.
4. Change `OWNER_USER`/`OWNER_PASSWORD` to the client's own login.
5. Re-skin the bot: edit the `BUSINESS` dict in `bot.py`
   (name, services, prices, hours, address) → `git push` → Render redeploys.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Webhook "Verify and Save" fails | URL must be https; token must match exactly; open /health to wake a sleeping free service, then retry |
| Bot doesn't reply | Check Render logs for errors; confirm subscribed to `messages`; confirm WHATSAPP_TOKEN is set |
| Dashboard 401 | Wrong OWNER_USER/PASSWORD |
| Reminders not sending | Dashboard → Reminder queue shows the scheduler heartbeat; without WHATSAPP_TOKEN they queue as manual drafts (by design) |
| "Invalid signature" in logs | APP_SECRET doesn't match Meta's app secret |

## What I (your agent) cannot do

- Create your Render, GitHub, or Meta accounts — they need your identity.
- Verify the client's business number — the SMS/call code goes to their phone.
- That's it. Everything else is done and tested.
