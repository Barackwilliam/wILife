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

### Deploy kwenye Render — huduma MOJA (Docker)

Django na bridge zinaendesha ndani ya huduma moja (`Dockerfile` + `start.sh`).
Bridge inasikiliza ndani tu (`127.0.0.1:3001`); haionekani nje.

1. Render → huduma yako ya wILife → **Settings**.
   - Kama kuna chaguo la **Runtime/Language**, badilisha kuwa **Docker**.
   - Kama halipo, tengeneza **New → Web Service → Docker** kutoka repo hii,
     hamisha Environment variables zote, kisha futa huduma ya zamani.
   - Build Command na Start Command ziache **tupu** (Dockerfile inashughulikia,
     ikiwemo `collectstatic` na `migrate`).
2. Ongeza kwenye Environment:

   ```
   WHATSAPP_ENABLED=true
   WHATSAPP_PROVIDER=baileys
   WHATSAPP_BRIDGE_KEY=<siri ndefu>
   AGENT_DEFAULT_RECIPIENT=2557XXXXXXXX     # namba yako binafsi (inapokea ujumbe)
   ```

   Usiweke `WHATSAPP_BRIDGE_URL` — image tayari inajua bridge iko wapi.
   Session ya WhatsApp inahifadhiwa kwenye Postgres ileile (kutoka `DB_*`),
   kwa hiyo deploy mpya haihitaji kuscan QR tena.
3. Deploy ikimaliza: ingia kama admin (superuser), fungua
   `https://your-app.onrender.com/agent/whatsapp/qr/`.
   Kwenye simu ya namba ya bridge: WhatsApp → Settings → Linked Devices →
   Link a Device → scan. Ukurasa utaonyesha **✅ Connected**.

### Render free tier

- Huduma moja inayokaa macho saa 24 ni ~saa 720 kwa mwezi — inatosha ndani
  ya saa 750 za bure.
- Pinger ileile inayogonga `/agent/tick/` (k.m. kila dakika 5–10) inaiweka
  huduma macho, na bridge nayo inabaki imeunganishwa.
- Kila deploy ya Django inaanzisha bridge upya kwa sekunde chache; ujumbe
  unaotumwa wakati huo unajaribiwa tena kwenye tick inayofuata.
- Baileys si API rasmi ya Meta. Kutuma ujumbe kwako mwenyewe kwa kiwango
  kidogo kuna hatari ndogo, lakini ndiyo sababu namba ya pili inapendekezwa.

### Njia mbadala: bridge kama huduma tofauti

Kama siku moja utataka bridge iwe huduma yake (k.m. plan ya Starter): Render →
New Web Service, Root Directory `whatsapp_bridge`, Build `npm install`, Start
`node server.js`, Environment `BRIDGE_API_KEY`, `DJANGO_URL`, `DATABASE_URL`.
Kisha kwenye Django weka `WHATSAPP_BRIDGE_URL=https://your-bridge.onrender.com`.
QR iko kwenye `https://your-bridge.onrender.com/qr?key=<BRIDGE_API_KEY>`.

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
