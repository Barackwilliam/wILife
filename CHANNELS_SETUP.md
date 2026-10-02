# Channels — WhatsApp (Baileys), Email, Telegram

Agent hutuma ukumbusho, brief ya asubuhi, review na rasimu za idhini kwako.
Ukiweka `AGENT_SELF_CHANNEL=all`, kila ujumbe unaenda kwenye **channel zote
zilizowekwa** kwa wakati mmoja. Channel moja ikishindwa, nyingine bado zinatuma.

Kukagua zote wakati wowote:

```bash
python manage.py channels_check          # nini kimewekwa, nini kinakosekana
python manage.py channels_check --send   # tuma ujumbe wa majaribio kwa kila channel
```

---

## 1. WhatsApp kupitia Baileys

Bridge (`whatsapp_bridge/`) ni huduma ndogo ya Node. Inaingia kwenye namba ya
WhatsApp kama "Linked Device", kama WhatsApp Web. Hakuna dirisha la saa 24 na
hakuna template za Meta.

```
Django ──POST /send──▶ bridge ──▶ WhatsApp ──▶ simu yako
simu yako ──"OK 1234"──▶ bridge ──▶ Django /agent/whatsapp/baileys/ ──▶ jibu
```

**Tumia namba ya pili (SIM nyingine) kwa bridge**, si namba yako binafsi.
Ukitumia namba ileile, ujumbe unaingia kwenye "Message yourself" bila arifa,
na majibu yako ya `OK 1234` hayatafika kwa agent.

### Deploy kwenye Render

1. Render → **New → Web Service** → chagua repo hii.
2. **Root Directory:** `whatsapp_bridge`
   **Build Command:** `npm install`
   **Start Command:** `node server.js`
3. Environment:

   | Variable | Thamani |
   |---|---|
   | `BRIDGE_API_KEY` | siri ndefu, sawa na `WHATSAPP_BRIDGE_KEY` ya Django |
   | `DJANGO_URL` | `https://your-app.onrender.com` |
   | `DATABASE_URL` | connection string ya Supabase (URI) — session inabaki baada ya deploy |
   | `SESSION_NAME` | `wilife` |

4. Deploy ikimaliza, fungua `https://your-bridge.onrender.com/qr?key=<BRIDGE_API_KEY>`.
   Kwenye simu ya namba ya bridge: WhatsApp → Settings → Linked Devices →
   Link a Device → scan. Ukurasa utaonyesha **✅ Connected**.

### Django (Render → Environment ya app kuu)

```
WHATSAPP_ENABLED=true
WHATSAPP_PROVIDER=baileys
WHATSAPP_BRIDGE_URL=https://your-bridge.onrender.com
WHATSAPP_BRIDGE_KEY=<sawa na BRIDGE_API_KEY>
AGENT_DEFAULT_RECIPIENT=2557XXXXXXXX     # namba yako binafsi (inapokea ujumbe)
```

Au weka namba yako kwenye Profile ndani ya wILife.

### Render free tier — muhimu

- Huduma za bure **hulala** baada ya dakika 15 bila maombi, na bridge ikilala
  WhatsApp inakatika. Weka pinger (k.m. cron-job.org) kwenye
  `https://your-bridge.onrender.com/health` kila dakika 10.
- Free tier ina saa 750 kwa mwezi **kwa huduma zote kwa pamoja**. Huduma mbili
  zikiwa macho saa 24 zinahitaji ~1,460, kwa hiyo moja wapo (bora bridge) iwe
  kwenye plan ya Starter, au iendeshwe kwenye seva nyingine (VPS, Railway, n.k.).
- Baileys si API rasmi ya Meta. Kutuma ujumbe kwako mwenyewe kwa kiwango
  kidogo kuna hatari ndogo, lakini ndiyo sababu namba ya pili inapendekezwa.

---

## 2. Email (Resend)

SMTP imezuiwa kwenye Render free, kwa hiyo email inatumwa kupitia Resend (HTTPS).

1. Fungua akaunti kwenye resend.com → **API Keys** → tengeneza key.
2. **Domains** → ongeza domain yako (k.m. `jamiitek.com`) na weka DNS records
   wanazokupa. Bila domain iliyothibitishwa, Resend inatuma tu kwa email
   uliyojisajili nayo.
3. Environment:

```
EMAIL_CHANNEL_ENABLED=true
RESEND_API_KEY=re_xxxxxxxx
AGENT_EMAIL_FROM=wILife <agent@jamiitek.com>
AGENT_EMAIL_TO=wewe@example.com
```

Maelezo zaidi: `EMAIL_SETUP.md`.

---

## 3. Telegram

1. Telegram → @BotFather → `/newbot` → pata token.
   (Kama token ya zamani ilishaonekana mahali popote, tumia `/revoke` kwanza.)
2. Weka `TELEGRAM_ENABLED=true` na `TELEGRAM_BOT_TOKEN`.
3. Tuma ujumbe wowote kwa bot yako, kisha endesha:
   `python manage.py telegram_setup` → itakuonyesha chat id → weka `AGENT_TELEGRAM_CHAT_ID`.
4. Tengeneza `TELEGRAM_WEBHOOK_SECRET` (siri ndefu), deploy, kisha:
   `python manage.py telegram_setup --webhook https://your-app.onrender.com`

Telegram inahitaji VPN Tanzania, ndiyo maana `all` ni chaguo zuri: email na
WhatsApp bado zinafika hata Telegram ikizuiwa.

---

## 4. Washa zote

```
AGENT_SELF_CHANNEL=all
```

Kisha `python manage.py channels_check --send`. Unapaswa kupokea ujumbe mmoja
kwenye kila channel.
