# wILife Agent — Step 0 + Step 1 Installation

**What this delivers:** secrets out of source code, and a working heartbeat that sends WhatsApp reminders without William touching anything.

Everything below was tested end to end against a copy of the real repo before delivery: migration applies, tick runs, idempotency holds, endpoint auth rejects bad tokens, and a failed send correctly releases its claim for retry.

---

## STEP 0 — Rotate credentials (do this first, before any code)

The old values are in git history. Changing the file is not enough — the old secrets must be revoked at the source.

1. **Supabase** → Project Settings → Database → reset the database password.
2. **Zoho** → change the mailbox password for `info@nyumbachap.online`.
3. Generate a fresh Django secret key:
   ```bash
   python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
   ```
4. Generate the agent tick token:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

If the repo is public, consider making it private. Rotating the secrets is what actually protects you; making it private just stops the bleeding.

---

## STEP 1 — Install

### 1. Copy in the new files

```
core/agent/__init__.py
core/agent/whatsapp.py
core/agent/jobs.py
core/management/__init__.py
core/management/commands/__init__.py
core/management/commands/run_agent_tick.py
core/views_agent.py
core/migrations/0005_agent_heartbeat.py
personal_assistant/settings.py     ← replaces the existing file
.env.example
.gitignore
```

### 2. Edit `core/models.py`

Three changes — see `core/models_APPEND_THIS.py` for the exact code.

- Add `whatsapp_number` to the existing `Profile` class
- Add a `Meta` class with the reminder index to the existing `Schedule` class
- Append the new `AgentRun` model at the end of the file

### 3. Edit `core/urls.py`

```python
from . import views_agent          # add near the other imports

# add inside urlpatterns, near the top:
path('healthz/', views_agent.healthz, name='healthz'),
path('agent/tick/', views_agent.agent_tick, name='agent_tick'),
```

### 4. Add to `requirements.txt`

```
python-dotenv==1.0.1
```

### 5. Create your `.env`

Copy `.env.example` to `.env` and fill in the real values from Step 0. **Do not commit it** — the supplied `.gitignore` already excludes it.

### 6. Migrate

```bash
pip install -r requirements.txt
python manage.py migrate
```

### 7. Set your WhatsApp number

Either through Django admin on your Profile, or:

```bash
python manage.py shell -c "
from django.contrib.auth.models import User
u = User.objects.get(username='YOUR_USERNAME')
u.profile.whatsapp_number = '0712345678'
u.profile.save()
print('ok')
"
```

The number is normalised automatically — `0712345678`, `+255 712 345 678` and `255712345678` all work.

---

## Verify locally before deploying

```bash
# 1. Find work without sending anything
python manage.py run_agent_tick --dry-run

# 2. Send for real
python manage.py run_agent_tick

# 3. Run again — should report "nothing due"
python manage.py run_agent_tick
```

---

## ACCEPTANCE TEST (from the spec — Step 1 is not done until this passes)

1. Create a Schedule with `reminder_datetime` five minutes from now.
2. Close the app. Close the browser. Do not touch anything.
3. Wait.
4. **A WhatsApp message arrives.**

That is the moment wILife stops being a record book.

---

## Deploy to Render

1. Set every variable from `.env.example` in the Render dashboard under **Environment**. Do not upload `.env`.
2. Keep the Procfile as it is — the tick is triggered over HTTP, not by a worker.
3. Set two UptimeRobot monitors:

| Monitor | URL | Interval | Purpose |
|---|---|---|---|
| Keep-alive | `https://YOUR-APP.onrender.com/healthz/` | 5 min | Stops Render spinning the service down |
| Agent tick | `https://YOUR-APP.onrender.com/agent/tick/?token=YOUR_TOKEN` | 15–30 min | Actually runs the heartbeat |

Reminders do not need 5-minute precision. A 15-minute tick with `reminder_datetime` set 30 minutes ahead of an event gives plenty of margin, and keeps the work off the keep-alive path.

---

## Design notes worth knowing before you change anything

**Claim-then-send.** Rows are locked with `SELECT ... FOR UPDATE SKIP LOCKED` and marked sent inside a very short transaction. The WhatsApp call happens *outside* that transaction. This means a slow network call never holds a database lock, and two overlapping ticks can never send the same reminder twice — which matters, because an external pinger can fire again before the previous run has finished.

**Failure releases the claim.** If a send fails, `reminder_sent` is set back to `False` and the next tick retries it. Nothing is silently lost. This is verified by test.

**Stale reminders are dropped, not sent.** Anything more than `AGENT_STALE_REMINDER_HOURS` (default 24) late is marked done without sending. After any outage you get silence, not a flood of yesterday's reminders.

**Time budget.** Each tick stops at `AGENT_TICK_BUDGET_SECONDS` (default 20) and releases whatever it did not reach. It always returns before a pinger's 30-second timeout. Unfinished work is deferred, never dropped.

**Retry on everything outbound.** Not because services sleep, but because Render restarts free instances at will and every deploy is about a minute of absence.

**A crashing job cannot stop the heartbeat.** `run_tick` catches exceptions per job and records them. Step 2's morning brief cannot take reminders down with it.

**`AgentRun` is your only window.** The agent works while nobody watches. Check this table to tell "nothing needed doing" apart from "it has been dead for three days". Register it in `core/admin.py` so you can see it without a shell.

---

## Permission tier — carried forward

Everything in `core/agent/jobs.py` is **TIER A**: it only ever messages William himself. Nothing in this file may be reused to message a client. When Tier B arrives in Step 3, drafts go through the dispatcher with an explicit approval gate — not through these functions.

---

## Then update the spec

Open `WILIFE_AGENT_SPEC.md`, Section 8, and mark Steps 0 and 1 complete with today's date. That log is what the next AI session reads first.
