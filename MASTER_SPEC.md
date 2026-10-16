You are my senior full-stack engineer, WhatsApp integration specialist, and QA lead. Build and complete “SlotKeeper — Missed Appointment Saver”: a reliable appointment system that books customers through WhatsApp, sends reminders, handles cancellations and rescheduling, and fills vacant appointments from a waiting list.

My priority is a working product with verified behavior. Implement the system, run it, test it, fix failures, and deploy the finished work within the permissions provided. Continue through routine implementation decisions without repeatedly asking me to approve them. Ask only for essential missing account details, credentials through secure channels, or decisions that materially change scope, cost, access, or existing data.

1. EXISTING PROJECT AND SCOPE

Existing app: https://slotkeeper-appointments.cheekyspool16.chatgpt.site

The initial implementation uses React, Vinext, Cloudflare Workers, D1, and ChatGPT authentication. It includes saved appointments, cancellation/rescheduling, a waiting list, draft messages, manual WhatsApp links, and an in-app booking preview. Live inbound WhatsApp handling, automatic sending, and a background scheduler are not yet implemented.

Inspect the actual source, migrations, deployment configuration, and current behavior before editing. Preserve working features, existing records, authentication, and private dashboard access. A live URL does not provide source-code access; if the source is unavailable, request the repository or source export.

Complete the existing app rather than creating an unrelated replacement. If starting without source, explain that limitation and choose one supported stack before implementing. Do not add billing or subscription plans unless I request them separately.

2. BUSINESS SETUP

Start with a dental clinic in Pakistan, while making business names, services, and hours configurable for salons and workshops.

Provide:
- Business name, contact details, location, and IANA timezone; default Asia/Karachi.
- Services with duration, optional price/currency, and eligible staff.
- Staff calendars, working days, breaks, holidays, closures, and booking buffers.
- Configurable booking horizon, minimum notice, cancellation cutoff, and reminder timing.
- Owner and staff permissions enforced on the server.
- Clear setup validation and helpful errors.

Store appointment instants in UTC and display them in the relevant business timezone. Clarify dates and times with customers. Test daylight-saving transitions if other timezones are enabled.

3. CUSTOMER BOOKING CONVERSATION

Implement a durable conversation state machine supporting English, Urdu, and Roman Urdu.

Example flow:
Greeting → choose service → choose staff or any available → choose date → retrieve real availability → select time → collect necessary customer details → obtain relevant messaging permission → show a complete summary → customer explicitly confirms → atomically create the booking → send the appropriate confirmation.

Support booking, confirming, rescheduling, cancelling, joining/leaving the waiting list, checking business hours, and requesting a human.

Use official interactive messages where supported and provide a text fallback. Handle incorrect inputs, abandoned conversations, expired sessions, repeated buttons, multiple active appointments, and changes of intent.

Identify the customer from verified incoming messaging context. Never let someone retrieve or change another customer’s booking by submitting a name or appointment ID. If someone has multiple bookings, ask which one they mean.

Keep scheduling decisions deterministic. If AI is used for language understanding, validate its structured output server-side. It must never invent availability, prices, policies, booking success, or delivery status. Ambiguous requests require clarification. Hand off unresolved requests to staff. For dental clinics, keep the assistant focused on administration rather than diagnosis or treatment advice.

4. BOOKING INTEGRITY

The database is the source of truth.

Prevent overlapping appointments across every write path: dashboard, WhatsApp, waiting-list acceptance, rescheduling, and imports.

For fixed slots, use a database-enforced reservation constraint. For variable durations, enforce overlapping interval protection with a proven transactional or serialized approach supported by the chosen database. A frontend check followed by an ordinary insert is insufficient.

Use short-lived slot holds only if needed. Make expiry and final confirmation authoritative on the server. Revalidate availability at commit time.

Make repeated booking requests idempotent. A reschedule must atomically obtain the new appointment time and release the old one; if it fails, preserve the original booking.

Define allowed appointment transitions explicitly. Do not mark a future appointment completed or missed. Record who made changes and when.

5. CANCELLATIONS AND WAITING-LIST RECOVERY

When a customer cancels:
- Confirm the exact booking and apply the configured policy.
- Release its reservation and invalidate obsolete reminder jobs.
- Find eligible waiting customers by service, staff, date/time preferences, permission, and documented queue order.
- Offer the opening to one eligible customer at a time.
- Use a configurable offer expiry and an explicit acceptance action.
- Validate that the accepting person is the intended recipient.
- Commit acceptance atomically only while the offer and slot remain valid.
- Release an expired or declined offer and move to the next eligible customer.
- Prevent parallel acceptance, duplicate bookings, and acceptance after cancellation or opt-out.
- Remove fulfilled waiting-list entries and send the relevant confirmation.

Track recovered appointments from actual bookings, excluding cancelled recoveries. Never fabricate saved revenue or performance statistics.

6. OFFICIAL WHATSAPP CONNECTION

Use the official WhatsApp Business Platform Cloud API or an explicitly selected supported provider. Consult current official Meta documentation before implementing version-dependent details.

Build an owner-only connection setup with truthful status:
Not configured → credentials saved → account validated → webhook verified → messaging test passed → live.

Support test-number setup first. For an existing WhatsApp Business app number, inspect current Coexistence eligibility and onboarding requirements. Explain any supported provider or account prerequisites. Do not delete, migrate, or deregister an existing number without explaining the impact and obtaining authorization.

Handle the account identifiers required by the current API, including the messaging account/WABA identifier and business phone-number ID. Keep access tokens, app secrets, and verification secrets server-side in supported secret storage. Mask saved secrets, exclude them from logs and browser bundles, and provide credential rotation/disconnection handling.

Use an appropriately provisioned production token with the required permissions. Do not present temporary development tokens as a permanent production setup.

Show accurate connection errors, including expired credentials, missing permissions, unverified numbers, unapproved templates, and unavailable onboarding features. Never display Connected based only on a populated form.

7. SECURE INBOUND WEBHOOKS

Provide a publicly reachable HTTPS webhook endpoint while keeping the administrative dashboard private.

If the current host cannot expose only the required webhook paths securely, deploy a separate supported ingress backend. Give it narrowly scoped authenticated access to scheduling operations. Do not make the entire private dashboard public or impersonate a browser user.

Implement:
- Meta’s GET verification handshake using the configured verify token and challenge.
- POST authenticity validation using HMAC-SHA256 over the raw request bytes, the app secret, and X-Hub-Signature-256.
- Safe rejection of missing, malformed, or invalid signatures.
- Routing by verified business account and recipient phone-number identifiers.
- Durable event storage and deduplication before acknowledgement.
- Fast acknowledgement, with durable queued processing afterward.
- Safe handling of batched events, duplicate deliveries, unsupported message types, and out-of-order status events.

If durable persistence fails, do not acknowledge the event as successfully accepted. Never trust a customer-supplied business identifier to select another business’s records.

8. OUTBOUND MESSAGES AND PERMISSION

Implement confirmation, reminder, rescheduling, cancellation, and waiting-list offer messages.

Follow current WhatsApp rules for the customer service window, approved templates, template categories, and customer opt-in. Do not assume all messaging is free or that a category is guaranteed to be approved.

Store permission evidence: business, customer, purpose, source, timestamp, and revocation. Support STOP, UNSUBSCRIBE, and equivalent clear opt-out requests. Immediately suppress relevant pending messages after revocation.

Provide a clear human escalation route. Pause automated conversational replies during staff handoff and resume only through a defined action.

Use an outbox with explicit states such as queued, processing, accepted by provider, delivered, read, failed, suppressed, and delivery uncertain. Distinguish provider acceptance from delivery. Manual “marked sent” records must remain distinguishable from API delivery.

Deduplicate local jobs and attempts. When a provider timeout leaves acceptance uncertain, reconcile through supported identifiers/status mechanisms or flag it for review; do not blindly retry and claim exactly-once external delivery.

9. REMINDERS THAT WORK WHILE THE APP IS CLOSED

Use a durable server scheduler and queue, not browser timers or an open laptop.

Allow configurable reminders, initially 24 hours and 2 hours before an appointment. Define behavior for short-notice bookings so past-due reminders do not arrive together.

Use unique job identities tied to booking version and reminder type. Claim jobs safely across concurrent workers. Before each send, recheck appointment status, current timing, permission, template eligibility, and whether the message was already handled.

Invalidate obsolete jobs after cancellation or rescheduling. Respect configured quiet hours without sending reminders after the appointment. Retry transient failures with bounded exponential backoff and jitter. Handle rate limits and provider retry guidance, with a dead-letter queue for unresolved failures.

Demonstrate that reminders run when the dashboard is closed and recover safely after worker restarts or scheduler downtime.

10. BUSINESS DASHBOARD

Preserve the existing visual direction and build responsive desktop/mobile screens for:
- Overview and today’s schedule.
- Searchable appointments with filters and calendar views.
- Booking, confirmation, cancellation, rescheduling, completion, and no-show actions.
- Waiting-list preferences and active offers.
- Conversations, staff handoff, and message delivery history.
- Reminder queue, failures, retries, and connection health.
- Business settings, services, staff, hours, templates, and permissions.
- CSV export with spreadsheet-formula injection protection.

Every visible action must work or clearly explain its unmet prerequisite. Include accessible labels, keyboard support, loading states, empty states, and actionable errors. Keep test data separate and clearly labelled.

11. DATA, PRIVACY, AND OPERATIONS

Use durable tables for businesses, memberships, staff, services, availability, customers, permissions, appointments, reservations, conversations, inbound events, outbox messages, reminder jobs, waiting-list entries, offers, and audit events as needed.

Enforce business membership and record ownership on every applicable operation. Derive identity from authenticated server context, not request-body claims. Isolate businesses’ credentials, calendars, customers, and webhook events.

Use schema migrations, validated inputs, parameterized queries, protected staff endpoints, appropriate request limits, and rate limiting. Minimize stored personal data and redact message content and secrets from operational logs. Document retention, deletion, backup, and restoration procedures.

Implement health checks and alerts for failed sending, invalid credentials, overdue jobs, queue backlog, and webhook processing errors. Avoid duplicate processing during deploys and restarts.

12. REQUIRED VERIFICATION

Write meaningful automated integration and end-to-end tests covering:
- Successful booking through WhatsApp and visibility in the dashboard.
- Two simultaneous customers requesting the same slot: only one succeeds.
- Variable-duration overlaps and buffer conflicts.
- Duplicate/replayed inbound events and repeated confirmation clicks.
- Safe rescheduling, including failed rescheduling preserving the original booking.
- Cancellation invalidating all obsolete reminders.
- FIFO waiting-list offers, expiry, decline, stale acceptance, and concurrent acceptance.
- Booking-specific authorization and cross-business isolation.
- Invalid/missing webhook signatures and invalid verification tokens.
- Opt-out suppressing pending messages.
- Template/session-window handling and rejected templates.
- Transient failures, rate limits, expired tokens, ambiguous provider timeouts, and worker restarts.
- Delivery statuses arriving more than once or out of order.
- Timezone boundaries, business closures, and short-notice bookings.
- Mobile navigation, keyboard interaction, empty states, and useful error messages.

Run type checks, appropriate automated tests, and a production build. Inspect the deployed flows using supported tools when available. Use only an owner-authorized test recipient for live messaging tests; do not message real customers during development.

Distinguish mocked tests, local integration tests, deployed tests, and real WhatsApp tests. Never claim a test passed if it was not run. When credentials or platform access block a live test, finish all independent implementation and report the specific unverified behavior.

13. IMPLEMENTATION WORKFLOW

First inspect the existing project and identify concrete gaps. Select a compatible architecture and explain material hosting or cost implications briefly. Then implement in dependency order: data integrity → WhatsApp ingress/outbox → booking conversations → cancellation/waitlist flow → reminders → dashboard → verification → deployment.

Keep changes focused. Fix root causes and add regression coverage for meaningful bugs. After a failure, inspect the evidence before changing approach. Do not disable authorization, signature validation, or failing integrity tests just to obtain a green result.

Maintain a short record of discovered problems, fixes, and verification so the same failures are not reintroduced.

14. DELIVERY AND DEFINITION OF DONE

Deliver:
- The working deployed application and accessible source.
- Database migrations and reproducible setup commands.
- An environment-variable example containing names and placeholders only.
- Exact WhatsApp setup instructions and the actual verified callback URL.
- Template drafts with variable mappings; label Meta approval as pending until verified.
- Scheduler/queue configuration and operating instructions.
- Test results showing what ran and what remains unverified.
- A concise list of remaining account approvals, credentials, platform limitations, and costs.
- Recovery, backup, and troubleshooting instructions.

Do not claim the live WhatsApp bot is complete until an authorized test message reaches the webhook, produces a real booking, appears in the dashboard, receives the correct WhatsApp response, and passes a scheduled reminder test with the dashboard closed. Validate cancellation and waiting-list recovery as well.

If external setup blocks completion, label the result accurately: “Application implemented; live activation pending [specific prerequisite].”

Begin by inspecting the project and carry the implementation through to a verified result.

---

## 13. MISSED-CALL TEXT-BACK (added 2026-10-16)

When a call goes unanswered, the missed-call notifier (phone app / telephony
webhook) POSTs to `/api/missed-call` with `{caller_phone, secret}`. The app
logs the call and instantly sends the caller a WhatsApp text-back inviting
them to book; the normal bot conversation takes over from their reply.

- Auth: shared `MISSED_CALL_SECRET` in the JSON body (timing-safe compare),
  or owner basic auth when no secret is configured.
- Anti-spam: the same caller gets at most one text-back per
  `MISSED_CALL_COOLDOWN_MIN` (default 30) minutes; repeats are logged as
  skipped.
- Abuse protection: per-IP rate limiting (`MISSED_CALL_RATE_PER_MIN`,
  default 30/min → HTTP 429), a daily cap on text-backs actually sent
  (`MISSED_CALL_DAILY_CAP`, default 200/day), and strict phone validation
  (7–15 digits only) so junk input can't burn WhatsApp quota.
- Manual mode: when the WhatsApp API is not configured, the endpoint returns
  the ready-to-forward draft text instead of sending.
- Dashboard: new "Missed calls" section + `missed_textbacks` count card;
  `GET /api/missed-calls` (owner-protected) lists the log.
- Message text: `bot.missed_call_textback()`, built from BUSINESS name/city.
- Tests: `tests/test_missed_call.py` — 15 checks (auth, cooldown, send path,
  manual fallback, dashboard render). Phase-2 suite still passes.

Remaining for production: the notifier itself (phone-side missed-call
detection or cloud telephony webhook) and the live WhatsApp credentials —
same activation prerequisites as the main bot.

## 14. OPT-OUT (STOP) HANDLING (added 2026-10-16)

Any customer can stop all bot messages by replying STOP (English and Roman
Urdu phrases: stop, unsubscribe, opt out, bas, bas karo, band karo,
nahi chahiye, message mat karo, …).

- The webhook checks every incoming message before the normal bot flow:
  opt-out phrase → recorded + confirmation sent; opted-out sender →
  silence (except START / shuru karo, which opts them back in).
- Honored everywhere: missed-call text-backs, scheduled 24h/2h reminders
  (marked cancelled, "customer opted out"), and manual dashboard sends
  (blocked with HTTP 409 until re-allowed).
- Owner dashboard: "Opted out" section listing opted-out numbers with a
  Re-allow button (`GET /api/opt-outs`, `POST /api/opt-outs/clear`,
  owner-protected). Re-allow only with the customer's clear permission.
- Tests: `tests/test_optout.py` — 14 checks (webhook STOP/START, silence,
  scheduler skip, manual block, dashboard APIs). All suites green.
