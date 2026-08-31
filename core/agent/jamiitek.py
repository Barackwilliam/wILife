"""
JamiiTek connector — Step 4.

Reads directly from JamiiTek's Postgres rather than calling its API. This is
deliberate (spec Section 4.2): Render free services sleep, Supabase does not.
An agent that has to wake a sleeping service before it can read anything will
spend most of its tick budget waiting, and will time out at 2am.

Reads here. Writes, when they come, go through JamiiTek's API — never through
this module.

CONFIGURATION REQUIRED
----------------------
The SQL below is driven by settings so you do not have to edit code to match
your schema. Set JAMIITEK_OVERDUE_INVOICE_SQL in the environment (or leave the
default and adjust it once here) to a query returning these column names:

    client_name, client_number, invoice_ref, amount, due_date

Verify it in psql BEFORE wiring it to anything that sends. Run
`python manage.py check_jamiitek` to test the connection and see the rows.
"""

import logging
from datetime import date

from django.conf import settings

log = logging.getLogger("core.agent")

DEFAULT_OVERDUE_SQL = """
    SELECT
        c.name          AS client_name,
        c.phone         AS client_number,
        i.reference     AS invoice_ref,
        i.total         AS amount,
        i.due_date      AS due_date
    FROM invoices i
    JOIN clients c ON c.id = i.client_id
    WHERE i.status <> 'paid'
      AND i.due_date < CURRENT_DATE
    ORDER BY i.due_date ASC
    LIMIT %(limit)s
"""


class JamiiTekUnavailable(Exception):
    """The connector is not configured, or the database could not be reached."""


def _dsn():
    dsn = getattr(settings, "JAMIITEK_DB_DSN", "")
    if not dsn:
        raise JamiiTekUnavailable(
            "JAMIITEK_DB_DSN is not set. Add the JamiiTek Postgres connection "
            "string to the environment, using a READ-ONLY database role."
        )
    return dsn


def _connect():
    try:
        import psycopg
        return psycopg.connect(_dsn(), connect_timeout=8)
    except ImportError:
        try:
            import psycopg2
            return psycopg2.connect(_dsn(), connect_timeout=8)
        except ImportError as exc:
            raise JamiiTekUnavailable("psycopg is not installed") from exc


def fetch_overdue_invoices(limit=20):
    """
    Return overdue invoices as a list of dicts.

    Read-only by construction: the query is a SELECT and the connection should
    use a role with SELECT rights only. Do not relax that — this module is one
    import away from every job the agent runs.
    """
    sql = getattr(settings, "JAMIITEK_OVERDUE_INVOICE_SQL", "") or DEFAULT_OVERDUE_SQL

    if "select" not in sql.lower().split()[0].lower():
        raise JamiiTekUnavailable("configured SQL is not a SELECT — refusing to run it")

    rows = []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"limit": limit})
                columns = [c[0] for c in cur.description]
                for record in cur.fetchall():
                    rows.append(dict(zip(columns, record)))
    except JamiiTekUnavailable:
        raise
    except Exception as exc:
        raise JamiiTekUnavailable(f"JamiiTek read failed: {exc}") from exc

    today = date.today()
    for row in rows:
        due = row.get("due_date")
        row["days_late"] = (today - due).days if due else 0
    return rows


def is_configured():
    return bool(getattr(settings, "JAMIITEK_DB_DSN", ""))
