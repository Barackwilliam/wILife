/**
 * Supabase (Postgres) Auth State kwa Baileys
 * ------------------------------------------------------------
 * Badala ya kuhifadhi session kwenye folda (auth_info_baileys),
 * tunaihifadhi kwenye database. Hii ni muhimu kwa Render na
 * hosting yoyote yenye filesystem ya muda — bila hii, session
 * inafutwa kila deploy na inabidi uscan QR upya.
 *
 * Matumizi:
 *   const { useSupabaseAuthState } = require('./supabaseAuth');
 *   const { state, saveCreds, clearAuth } =
 *       await useSupabaseAuthState(process.env.DATABASE_URL, 'kilimoni');
 */

const { Pool } = require('pg');

const {
    initAuthCreds,
    BufferJSON,
    proto,
} = require('@whiskeysockets/baileys');


async function useSupabaseAuthState(connectionString, sessionName = 'default') {

    if (!connectionString) {
        throw new Error('DATABASE_URL haijawekwa kwa Supabase auth state');
    }

    const pool = new Pool({
        connectionString,
        ssl: { rejectUnauthorized: false },
        max: 3,
    });

    // Jedwali la session — linaundwa lenyewe mara ya kwanza
    await pool.query(`
        CREATE TABLE IF NOT EXISTS baileys_auth (
            session_name TEXT NOT NULL,
            key_id       TEXT NOT NULL,
            value        JSONB,
            updated_at   TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (session_name, key_id)
        )
    `);


    // ── Msaidizi wa kusoma / kuandika ─────────────────

    async function readData(keyId) {
        try {
            const res = await pool.query(
                'SELECT value FROM baileys_auth WHERE session_name = $1 AND key_id = $2',
                [sessionName, keyId]
            );
            if (!res.rows.length) return null;
            return JSON.parse(JSON.stringify(res.rows[0].value), BufferJSON.reviver);
        } catch (e) {
            console.log('[auth] read error:', keyId, e.message);
            return null;
        }
    }

    async function writeData(keyId, value) {
        try {
            const json = JSON.parse(JSON.stringify(value, BufferJSON.replacer));
            await pool.query(
                `INSERT INTO baileys_auth (session_name, key_id, value, updated_at)
                 VALUES ($1, $2, $3, NOW())
                 ON CONFLICT (session_name, key_id)
                 DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()`,
                [sessionName, keyId, json]
            );
        } catch (e) {
            console.log('[auth] write error:', keyId, e.message);
        }
    }

    async function removeData(keyId) {
        try {
            await pool.query(
                'DELETE FROM baileys_auth WHERE session_name = $1 AND key_id = $2',
                [sessionName, keyId]
            );
        } catch (e) {
            console.log('[auth] delete error:', keyId, e.message);
        }
    }


    // ── Pakua creds zilizopo, au tengeneza mpya ───────

    const creds = (await readData('creds')) || initAuthCreds();


    const state = {
        creds,
        keys: {
            get: async (type, ids) => {
                const data = {};
                await Promise.all(ids.map(async (id) => {
                    let value = await readData(`${type}-${id}`);
                    if (type === 'app-state-sync-key' && value) {
                        value = proto.Message.AppStateSyncKeyData.fromObject(value);
                    }
                    data[id] = value;
                }));
                return data;
            },
            set: async (data) => {
                const tasks = [];
                for (const type in data) {
                    for (const id in data[type]) {
                        const value = data[type][id];
                        const keyId = `${type}-${id}`;
                        tasks.push(value ? writeData(keyId, value) : removeData(keyId));
                    }
                }
                await Promise.all(tasks);
            },
        },
    };


    return {
        state,

        saveCreds: async () => {
            await writeData('creds', state.creds);
        },

        // Inatumika baada ya logout ili session ianze upya
        clearAuth: async () => {
            await pool.query(
                'DELETE FROM baileys_auth WHERE session_name = $1',
                [sessionName]
            );
            console.log('[auth] session imefutwa:', sessionName);
        },

        close: async () => {
            await pool.end();
        },
    };
}


module.exports = { useSupabaseAuthState };
