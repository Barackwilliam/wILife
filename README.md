# wILife

Personal life assistant built with Django. Tracks income, expenses, tasks,
health, schedules, goals and menstrual cycles, and runs a background agent
that sends briefs and reminders over Telegram, WhatsApp or email.

## Features

- **Finance** — income and expenses with categories; Excel and PDF export
  (`/export/excel/`, `/export/pdf/`)
- **Tasks, schedules, calendar** — with notifications
- **Health** — weight, exercise, sleep records
- **Goals** — milestones and progress updates
- **Menstrual cycle** — records and calendar
- **Agent** (`core/agent/`) — morning brief, schedule reminders, expiry watch,
  goals, weekly review, invoice watch, LeadScout, approvals. Most features are
  off by default; see `ENABLE_GUIDE.md` for the switch-on order.

## Stack

- Python 3.12, Django 5.2, Django REST Framework + SimpleJWT
- PostgreSQL (Supabase)
- pandas / openpyxl (Excel), xhtml2pdf (PDF)
- Gunicorn + WhiteNoise on Render

## Local setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp env.example.txt .env        # fill in real values
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The database is Postgres only — `DB_USER`, `DB_PASSWORD` and `DB_HOST` are
required. Never commit `.env`.

## Agent

The agent does its work in short "ticks". Trigger them either way:

- **HTTP** — an external pinger (e.g. cron-job.org) calls `/agent/tick/` with
  the `X-Agent-Token` header set to `AGENT_TICK_TOKEN`
- **CLI** — `python manage.py run_agent_tick`

`/healthz/` is an unauthenticated liveness check. The `AgentRun` table
(Django admin) records every tick — check it to confirm the agent is alive.

Channel webhooks: `/agent/telegram/` and `/agent/whatsapp/`.

Useful management commands:

| Command | Purpose |
|---|---|
| `telegram_setup` | Register the Telegram webhook |
| `email_setup` | Verify the email channel |
| `check_expiry`, `check_goals`, `check_jamiitek`, `check_leadscout` | Dry-run individual agent jobs |

## Further docs

- `STEP1_INSTALL.md` — agent heartbeat install
- `ENABLE_GUIDE.md` — enabling goals, weekly review, invoices, LeadScout, Groq
- `EMAIL_SETUP.md` — email channel (Resend)
- `EXPIRY_INSTALL.md` — expiry watch
