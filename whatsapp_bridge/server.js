/**
 * wILife WhatsApp bridge (Baileys)
 * ------------------------------------------------------------
 * Logs in to a normal WhatsApp number as a linked device and gives Django
 * two things:
 *
 *   POST /send            Django -> WhatsApp   { to, text }      (X-Bridge-Key)
 *   inbound messages      WhatsApp -> Django   /agent/whatsapp/baileys/
 *
 * Django handles all decisions (who may approve what). This bridge only moves
 * text in both directions.
 *
 * Other endpoints:
 *   GET /health           liveness, no auth — point the uptime pinger here
 *   GET /status           connection state, no auth, no secrets
 *   GET /qr?key=...       page with the QR code to link the number
 *                         (in the single-service Docker setup, open
 *                         /agent/whatsapp/qr/ on the Django site instead)
 *   POST /logout          unlink the number                (X-Bridge-Key)
 */

require('dotenv').config();

const express = require('express');
const axios = require('axios');
const crypto = require('crypto');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const { Boom } = require('@hapi/boom');
const {
    default: makeWASocket,
    DisconnectReason,
    useMultiFileAuthState,
} = require('@whiskeysockets/baileys');

const { useSupabaseAuthState } = require('./supabaseAuth');

const PORT = process.env.PORT || 3001;
const HOST = process.env.BRIDGE_HOST || '0.0.0.0';
const DJANGO_URL = (process.env.DJANGO_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const BRIDGE_API_KEY = process.env.BRIDGE_API_KEY || '';
const SESSION_NAME = process.env.SESSION_NAME || 'wilife';

if (!BRIDGE_API_KEY) {
    console.error('BRIDGE_API_KEY is not set. Refusing to start without it.');
    process.exit(1);
}

const app = express();
app.use(express.json({ limit: '64kb' }));

let sock = null;
let auth = null;
let connectionStatus = 'starting';
let connectedNumber = '';
let currentQrDataUrl = '';
let currentQrAt = 0;


function keyOk(provided) {
    const a = Buffer.from(String(provided || ''));
    const b = Buffer.from(BRIDGE_API_KEY);
    return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function requireKey(req, res, next) {
    if (!keyOk(req.headers['x-bridge-key'])) {
        return res.status(401).json({ success: false, error: 'Unauthorized' });
    }
    next();
}

function toJid(number) {
    const digits = String(number || '').replace(/\D/g, '');
    return digits ? `${digits}@s.whatsapp.net` : '';
}


// ============================================================
// HTTP API
// ============================================================

app.get('/health', (req, res) => {
    res.json({ success: true, service: 'wILife WhatsApp Bridge', status: connectionStatus });
});

app.get('/status', (req, res) => {
    res.json({
        success: true,
        status: connectionStatus,
        connected: connectionStatus === 'connected',
    });
});

app.post('/send', requireKey, async (req, res) => {
    const jid = toJid(req.body?.to);
    const text = String(req.body?.text || '').slice(0, 4000);

    if (!jid || !text) {
        return res.status(400).json({ success: false, error: 'to and text are required' });
    }
    if (connectionStatus !== 'connected' || !sock) {
        // 503 tells Django to retry — the bridge may still be reconnecting.
        return res.status(503).json({ success: false, error: `not connected (${connectionStatus})` });
    }

    try {
        const [found] = await sock.onWhatsApp(jid);
        if (!found?.exists) {
            return res.status(404).json({ success: false, error: `${req.body.to} is not on WhatsApp` });
        }
        const sent = await sock.sendMessage(found.jid || jid, { text });
        res.json({ success: true, id: sent?.key?.id || 'sent' });
    } catch (e) {
        console.error('send failed:', e.message);
        res.status(502).json({ success: false, error: e.message });
    }
});

app.get('/qr', (req, res) => {
    if (!keyOk(req.query.key)) {
        return res.status(401).send('Unauthorized');
    }

    let body;
    if (connectionStatus === 'connected') {
        body = `<h2>✅ Connected</h2><p>Number: +${connectedNumber}</p>`;
    } else if (currentQrDataUrl) {
        const age = Math.round((Date.now() - currentQrAt) / 1000);
        body = `<h2>Scan with WhatsApp</h2>
            <p>WhatsApp → Settings → Linked Devices → Link a Device</p>
            <img src="${currentQrDataUrl}" alt="QR code" width="320" height="320">
            <p>QR age: ${age}s (a new one appears every ~20s)</p>`;
    } else {
        body = `<h2>Waiting for QR…</h2><p>Status: ${connectionStatus}</p>`;
    }

    res.set('Cache-Control', 'no-store');
    res.send(`<!doctype html><html><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <meta http-equiv="refresh" content="10"><title>wILife WhatsApp</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:24px">${body}</body></html>`);
});

app.post('/logout', requireKey, async (req, res) => {
    try {
        if (sock) await sock.logout();
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: String(e) });
    }
});


// ============================================================
// INBOUND MESSAGES -> DJANGO
// ============================================================

function extractText(message) {
    const m = message.message || {};
    return (
        m.conversation ||
        m.extendedTextMessage?.text ||
        m.imageMessage?.caption ||
        m.videoMessage?.caption ||
        ''
    ).trim();
}

async function senderPhone(remoteJid, message) {
    // WhatsApp may hide the number behind a LID. Try to recover it; if we
    // cannot, Django will not recognise the sender and will stay silent.
    let jid = remoteJid;
    if (remoteJid.endsWith('@lid')) {
        let pn = message.key.senderPn || '';
        if (!pn) {
            try {
                pn = (await sock.signalRepository?.lidMapping?.getPNForLID?.(remoteJid)) || '';
            } catch (e) {
                pn = '';
            }
        }
        if (pn) jid = pn;
    }
    // Strip the device suffix: "255712345678:0@s.whatsapp.net" -> "255712345678"
    return jid.split('@')[0].split(':')[0].replace(/\D/g, '');
}

async function handleIncoming(event) {
    if (event.type !== 'notify') return;

    for (const message of event.messages) {
        try {
            const remoteJid = message.key.remoteJid || '';
            if (message.key.fromMe) continue;
            if (remoteJid === 'status@broadcast' || remoteJid.endsWith('@g.us')) continue;

            const text = extractText(message);
            if (!text) continue;

            const phone = await senderPhone(remoteJid, message);
            if (!phone) continue;

            const response = await axios.post(
                `${DJANGO_URL}/agent/whatsapp/baileys/`,
                { phone, message: text, message_id: message.key.id || '' },
                { headers: { 'X-Bridge-Key': BRIDGE_API_KEY }, timeout: 60000 }
            );

            const reply = response.data?.reply;
            if (reply) {
                await sock.sendMessage(remoteJid, { text: String(reply) });
            }
        } catch (error) {
            if (error.response) {
                console.error('Django error:', error.response.status, error.response.data);
            } else {
                console.error('inbound handling failed:', error.message);
            }
        }
    }
}


// ============================================================
// WHATSAPP CONNECTION
// ============================================================

async function loadAuth() {
    if (auth?.close) await auth.close().catch(() => {});

    if (process.env.DATABASE_URL) {
        // Session lives in Postgres, so a redeploy does not require a new QR scan.
        auth = await useSupabaseAuthState(process.env.DATABASE_URL, SESSION_NAME);
        console.log(`Auth: Postgres (session "${SESSION_NAME}")`);
    } else {
        const local = await useMultiFileAuthState('./auth_info_baileys');
        auth = { ...local, clearAuth: null, close: null };
        console.log('Auth: local folder ./auth_info_baileys');
    }
}

async function connectToWhatsApp() {
    sock = makeWASocket({
        auth: auth.state,
        markOnlineOnConnect: false,
        syncFullHistory: false,
        browser: ['wILife', 'Chrome', '1.0.0'],
    });

    sock.ev.on('creds.update', auth.saveCreds);
    sock.ev.on('messages.upsert', handleIncoming);

    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
        if (qr) {
            connectionStatus = 'waiting_qr';
            qrcodeTerminal.generate(qr, { small: true });
            console.log('Scan the QR above, or open /qr?key=<BRIDGE_API_KEY>');
            try {
                currentQrDataUrl = await QRCode.toDataURL(qr, { width: 320, margin: 2 });
                currentQrAt = Date.now();
            } catch (e) {
                console.error('QR image error:', e.message);
            }
        }

        if (connection === 'open') {
            connectionStatus = 'connected';
            currentQrDataUrl = '';
            connectedNumber = (sock.user?.id || '').split(':')[0];
            console.log(`WhatsApp connected as +${connectedNumber}`);
        }

        if (connection === 'close') {
            connectionStatus = 'disconnected';
            const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;

            if (statusCode === DisconnectReason.loggedOut) {
                console.log('Logged out — clearing session; a new QR will follow.');
                connectedNumber = '';
                if (auth.clearAuth) await auth.clearAuth();
                await loadAuth();
            } else {
                console.log(`Connection closed (${statusCode}) — reconnecting.`);
            }
            setTimeout(() => connectToWhatsApp().catch(console.error), 3000);
        }
    });
}


app.listen(PORT, HOST, () => {
    console.log(`wILife WhatsApp bridge on ${HOST}:${PORT}, Django at ${DJANGO_URL}`);
});

loadAuth()
    .then(connectToWhatsApp)
    .catch((error) => {
        console.error('Failed to start WhatsApp connection:', error);
        process.exit(1);
    });
