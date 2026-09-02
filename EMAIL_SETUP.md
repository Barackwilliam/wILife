# Email Channel — Setup

Telegram needs a VPN in Tanzania. WhatsApp refuses machine-sent messages outside its 24-hour window. Email has neither problem: it works today, everywhere, free.

Uses Resend over HTTPS rather than SMTP, because Render's free tier blocks ports 25, 465 and 587.

Tested before delivery. Results at the bottom.

---

## 1. Resend account

1. Sign up at resend.com — free tier is 3,000 emails/month, far more than you need
2. **Domains → Add Domain →** `wlife.online`
3. Add the DNS records it gives you at your registrar
4. Wait for "Verified"
5. **API Keys → Create** → copy it

**If you cannot wait for DNS:** Resend lets you send without a verified domain, but only to the address you signed up with, using `onboarding@resend.dev` as the sender. That is enough to get running today — set `AGENT_EMAIL_FROM=onboarding@resend.dev` and `AGENT_EMAIL_TO=` your signup address, then switch to your own domain later.

## 2. Files

New:
```
core/agent/email_channel.py
core/management/commands/email_setup.py
```

Replace:
```
core/agent/channels.py        adds email + fallback between channels
core/agent/brief.py           recipient handling for the email channel
```

No migration.

## 3. settings.py

```python
# Self channel: email | telegram | whatsapp
AGENT_SELF_CHANNEL = env("AGENT_SELF_CHANNEL", "email")

# Email channel (Resend over HTTPS — SMTP ports are blocked on Render free)
EMAIL_CHANNEL_ENABLED = env_bool("EMAIL_CHANNEL_ENABLED", False)
RESEND_API_KEY = env("RESEND_API_KEY", "")
AGENT_EMAIL_FROM = env("AGENT_EMAIL_FROM", "")
AGENT_EMAIL_TO = env("AGENT_EMAIL_TO", "")
```

## 4. Environment

```
AGENT_SELF_CHANNEL=email
EMAIL_CHANNEL_ENABLED=true
RESEND_API_KEY=re_...
AGENT_EMAIL_FROM=wilife@wlife.online
AGENT_EMAIL_TO=your@email.com
```

Keep the Telegram variables. They cost nothing and become the automatic fallback if email fails — and they work normally whenever you are on a VPN or outside Tanzania.

## 5. Verify

```bash
python manage.py email_setup            # shows config and the channel actually in use
python manage.py email_setup --preview  # prints the HTML without sending
python manage.py email_setup --test     # sends a real test message
```

`--test` works from your laptop. Unlike Telegram, `api.resend.com` is not blocked.

---

## How routing works now

```
send_to_self(user, text)     → AGENT_SELF_CHANNEL, falling through to any
                               other configured channel if it fails
send_to_other(number, text)  → WhatsApp only. Never email, never Telegram.
```

Every job calls `send_to_self`. Exactly one function calls `send_to_other`: `approvals.execute_approved()`. Your clients are on WhatsApp and stay there.

**Fallback is deliberate.** If Resend rejects a message at 6am, the brief goes out on Telegram instead of vanishing. A reminder that arrives by the second-choice channel is still a reminder. The log records which channel was used.

**No job knows which channel is in use.** Messages are written once in WhatsApp-style markup — `*bold*`, `_italic_` — and the email sender converts it to HTML. Switching channels later changes one environment variable, not any job.

---

## Test results

```
ROUTING
  preferred channel selected              email
  message delivered with correct from/to  ✓
  plain-text alternative included         ✓

FORMATTING
  *bold*   → <strong>                     ✓
  _italic_ → <em>                         ✓
  newlines → <br>                         ✓
  HTML in message content escaped         ✓  (<script> neutralised)

SUBJECT LINE
  "⏰ *Kumbusho*"                → "⏰ Kumbusho"
  "☀️ *Habari za asubuhi…*"     → "☀️ Habari za asubuhi, William"
  "🚨 *IMEISHA MUDA* (2)"       → "🚨 IMEISHA MUDA (2)"
  message with no headline      → first non-empty line
  empty message                 → "wILife"

FALLBACK
  email fails, Telegram configured    delivered via Telegram, logged
  both fail                           DeliveryError naming both causes
  nothing configured                  DeliveryError with the fix in the message

ISOLATION
  send_to_other calls send_whatsapp only  ✓

FULL TICK
  ok, 0 failed, all seven jobs reporting
```

---

## One caveat worth knowing

Email does not interrupt you the way a message does. A reminder 30 minutes before a meeting only helps if you see it.

Turn on push notifications for that mailbox on your phone, or use an address that already notifies you. Otherwise the agent works perfectly and you still miss the meeting — and that failure looks like a bug when it is not.
