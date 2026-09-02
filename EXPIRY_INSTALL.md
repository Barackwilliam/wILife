# Expiry Watch — Hosting & Domains

The most time-critical job in the system, and the one that matters most while you are away.

Tested end to end against a real PostgreSQL 16 database using your table shapes and a read-only role. Results at the bottom.

---

## Why this exists

`ManagedWebsite.auto_suspend_on_expiry` defaults to `True`. A client's site can be suspended automatically while you are travelling, and the first you hear of it is an angry phone call. Domains are worse — an expired `.co.tz` is not always recoverable.

An overdue invoice waits. An expired domain does not.

---

## Install

### New files
```
core/agent/expiry.py
core/management/commands/check_expiry.py
```

### Replace
```
core/agent/jobs.py          registers expiry_watch, ahead of invoice_watch
```

### settings.py

```python
# Hosting / domain expiry watch
AGENT_EXPIRY_WATCH_ENABLED = env_bool("AGENT_EXPIRY_WATCH_ENABLED", False)
AGENT_EXPIRY_WATCH_HOUR = int(env("AGENT_EXPIRY_WATCH_HOUR", "7"))
AGENT_HOSTING_WARN_DAYS = int(env("AGENT_HOSTING_WARN_DAYS", "7"))
AGENT_DOMAIN_WARN_DAYS = int(env("AGENT_DOMAIN_WARN_DAYS", "30"))
JAMIITEK_EXPIRY_SQL = env("JAMIITEK_EXPIRY_SQL", "")
```

No migration. Uses the same `JAMIITEK_DB_DSN` as the invoice watch.

### Verify, then enable

```bash
python manage.py check_expiry
```

Compare against your records. When it matches:

```
AGENT_EXPIRY_WATCH_ENABLED=true
```

The default SQL is built in — you only need `JAMIITEK_EXPIRY_SQL` if your table prefix is not `app_`.

---

## What it sends

```
🚨 IMEISHA MUDA (2)

  Mbossai Milk
  Hosting · Benon Rwakatare · siku 4 zilizopita
  ⚠️ Itasimamishwa yenyewe
  📞 0765222333

  mbossai.co.tz
  Domain · Benon Rwakatare · siku 2 zilizopita
  📞 0765222333

⏳ Inakaribia (2)

  🔴 Africanberty Tips — siku 2
     Hosting · Salum Magere · TZS 30,000
  🟡 africanberty.co.tz — siku 12
     Domain · Salum Magere · TZS 45,000

Domain iliyoisha muda si mara zote inarudi. Shughulikia hizi kwanza.
```

The client's phone number is included on expired items only — so that acting on it is one tap, not a search through the portal.

---

## Two deliberate differences from the other jobs

**It repeats daily while something is expired.** Every other daily job writes an `AgentRun` marker and goes quiet until tomorrow. This one writes the marker *only when nothing is actually expired*. Anything past its date reports again every single day until it is resolved.

A one-time mention of an expired domain is exactly the message that gets scrolled past on a busy morning. Verified by test: with expired items present, three successive ticks all send, and no marker row is written.

**It escalates by urgency, not by date order alone.** Expired items outrank pending ones, and an expired site that will auto-suspend outranks an expired one that will not.

It also runs at 07:00 by default — an hour before the invoice watch, and before the working day starts.

---

## Test results

Against PostgreSQL 16, real tables, real read-only role:

```
QUERY
  hosting expired 4 days, auto-suspend        found, flagged
  domain expired 2 days                       found
  hosting due in 2 days                       found, marked 🔴
  domain due in 12 days                       found, marked 🟡
  hosting 60 days out                         correctly excluded
  domain 200 days out                         correctly excluded
  terminated website                          correctly excluded

BEHAVIOUR
  before 07:00                                "too early"
  08:00 with expired items                    sends
  09:00 same day, still expired               sends again  ← intended
  AgentRun marker while expired               not written  ← intended

SECURITY
  read-only role SELECT                       works
  read-only role UPDATE                       permission denied  ✓

DEFAULTS
  expiry_watch                                disabled
  full tick                                   ok, 0 failed
```

---

## Order to enable, given you are travelling

1. `check_jamiitek` → confirm invoice columns
2. `check_expiry` → confirm hosting and domain columns
3. `AGENT_EXPIRY_WATCH_ENABLED=true` ← **this one first, it is the urgent one**
4. `AGENT_INVOICE_WATCH_ENABLED=true`
5. Leave `AGENT_INVOICE_DRAFTS_ENABLED=false` until you are back

Steps 3 and 4 only ever message you. Step 5 is the only switch that can reach a client, and it should not be flipped the day before a trip.
