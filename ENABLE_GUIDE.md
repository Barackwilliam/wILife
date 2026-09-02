# wILife Agent — Everything Else, Built and Switched Off

Four additions. All disabled by default. Turn them on one at a time, in the order below.

Tested against a copy of your repo before delivery. Test results at the bottom.

---

## Install

### New files
```
core/agent/goals.py                              Goal / GoalMilestone reader
core/agent/review.py                             weekly 80/20 review
core/agent/leadscout.py                          read-only LeadScout connector
core/agent/leads.py                              lead watch job
core/management/commands/check_goals.py
core/management/commands/check_leadscout.py
```

### Replace
```
core/agent/jobs.py                               registers lead_watch + weekly_review
core/agent/brief.py                              adds the goals section
```

No migration. No model changes.

### settings.py

```python
# Goals in the morning brief
AGENT_GOALS_ENABLED = env_bool("AGENT_GOALS_ENABLED", False)
AGENT_GOAL_WARNING_DAYS = int(env("AGENT_GOAL_WARNING_DAYS", "3"))
AGENT_GOAL_TITLE_FIELD = env("AGENT_GOAL_TITLE_FIELD", "")
AGENT_GOAL_DATE_FIELD = env("AGENT_GOAL_DATE_FIELD", "")
AGENT_GOAL_DONE_FIELD = env("AGENT_GOAL_DONE_FIELD", "")

# Weekly review
AGENT_WEEKLY_REVIEW_ENABLED = env_bool("AGENT_WEEKLY_REVIEW_ENABLED", False)
AGENT_WEEKLY_REVIEW_WEEKDAY = int(env("AGENT_WEEKLY_REVIEW_WEEKDAY", "6"))   # 0=Mon 6=Sun
AGENT_WEEKLY_REVIEW_HOUR = int(env("AGENT_WEEKLY_REVIEW_HOUR", "18"))

# LeadScout
LEADSCOUT_DB_DSN = env("LEADSCOUT_DB_DSN", "")
LEADSCOUT_NEW_LEADS_SQL = env("LEADSCOUT_NEW_LEADS_SQL", "")
AGENT_LEAD_WATCH_ENABLED = env_bool("AGENT_LEAD_WATCH_ENABLED", False)
AGENT_LEAD_WATCH_HOUR = int(env("AGENT_LEAD_WATCH_HOUR", "9"))
AGENT_LEAD_MIN_SCORE = int(env("AGENT_LEAD_MIN_SCORE", "60"))
AGENT_LEAD_MAX_REPORTED = int(env("AGENT_LEAD_MAX_REPORTED", "5"))
```

Deploy. Nothing changes — every new job reports `disabled`. That is correct.

---

# SWITCH-ON ORDER

## Week 1 — nothing

UptimeRobot only. Confirm the morning brief arrives three days running.

## Week 2 — Goals

```bash
python manage.py check_goals
```

**Read the output before enabling.** I have not seen your models, so `goals.py` detects field names at runtime by matching against common names. The command shows exactly which fields it matched.

If it matched wrongly, override in settings — do not edit `goals.py`:

```
AGENT_GOAL_TITLE_FIELD=your_field
AGENT_GOAL_DATE_FIELD=your_field
AGENT_GOAL_DONE_FIELD=your_field
```

Then:
```
AGENT_GOALS_ENABLED=true
```

Milestones appear in the morning brief: overdue, due today, and due within `AGENT_GOAL_WARNING_DAYS` (default 3). The window is wider than one day on purpose — a milestone you learn about on the morning it is due is not a warning.

## Week 3 — Weekly review

```
AGENT_WEEKLY_REVIEW_ENABLED=true
```

Sunday 18:00. Preview it any time:

```bash
python manage.py shell -c "
from django.contrib.auth.models import User
from core.agent.review import build_review
print(build_review(User.objects.first()))
"
```

The useful line is not the totals — it is *"2 of 3 sources produce 80%"*. That is the concentration you cannot see by scrolling transactions.

## Week 4 — JamiiTek invoices

```bash
python manage.py check_jamiitek
```

Read-only Postgres role. SELECT rights only, not your app role. Adjust `JAMIITEK_OVERDUE_INVOICE_SQL` until the columns are right, then:

```
AGENT_INVOICE_WATCH_ENABLED=true
```

Reporting only. Drafts stay off.

## Week 5 — LeadScout

```bash
python manage.py check_leadscout
```

Query must return: `business_name, website, score, contact, found_at`. Then:

```
AGENT_LEAD_WATCH_ENABLED=true
AGENT_LEAD_MIN_SCORE=60
```

Only leads above the threshold are reported, up to five a day. A daily list of forty low-scoring leads is not information — it teaches you to ignore the channel.

## Week 6 — Groq

```
AGENT_BRAIN_ENABLED=true
GROQ_API_KEY=<key>
```

Falls back to templates on any failure.

## Week 7 — Client drafts

Last, and only if weeks 4 and 6 have been clean.

```
AGENT_INVOICE_DRAFTS_ENABLED=true
AGENT_INVOICE_DRAFT_MAX=3
```

This is the first switch that can reach a client. Everything before it only ever reaches you.

---

# Design notes

**Goals detection is a guess, and it is fenced.** Wrapped in try/except at the call site in `brief.py`, so a schema surprise degrades to a missing section rather than a missing brief. Verified by test: with the models absent entirely, the brief still builds.

**Weekly review computes concentration, not just totals.** It reports how many sources account for 80% of income. One client at 67% is a different business from three at 33% each, and only one of those readings should worry you.

**Lead watch is opinionated by design.** Threshold plus a daily cap. Reporting everything is the same as reporting nothing.

**Both new connectors refuse non-SELECT SQL** before it reaches the database, same as the JamiiTek one.

**Every new job defaults to off**, so deploying this changes nothing until you decide it should.

---

# Test results

```
GOALS DETECTION (against a simulated schema)
  GoalMilestone found    title->title  date->due_date  done->is_completed
  Goal found             title->title  date->target_date  done->is_completed
  overdue / today / upcoming rendered correctly
  completed milestone excluded
  far-future milestone excluded
  models absent            brief still builds, section omitted
  fields unrecognised      returns empty, no crash

WEEKLY REVIEW
  income by source with percentage shares
  "2 of 3 sources produce 80%"
  expenses by category
  net position, negative case called out explicitly
  tasks carried over, with day counts

DEFAULTS
  lead_watch      disabled
  weekly_review   disabled
  goals section   omitted
  invoice_watch   not configured
```

---

# Still not built

**Approval flow has never run end to end with a real draft.** The webhook works, the tier gate works, both tested — but no job has created a real approval yet. Week 7 is the first time that path runs for real. Expect to find something.

**Nothing uses `tools.dispatch()` yet.** The tool layer is exercised by tests, not by jobs. It becomes load-bearing when Groq starts choosing between drafts.

---

# Update the spec

`WILIFE_AGENT_SPEC.md` Section 8. Mark steps 0, 1, 2 complete; 4, 5, 6 as built-but-disabled. Add the four models missing from Section 3.1, plus `AgentRun` and `ApprovalRequest`. Record in "Decisions changed": self-facing channel is Telegram, not WhatsApp, because of Meta's 24-hour window.
