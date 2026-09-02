"""
LeadScout — new leads, summarised.

Same shape as the JamiiTek connector and for the same reason: read straight from
Postgres rather than calling a Render service that may be cold. Configured by
settings so you never edit Python to match your schema.

Your query must return these column names:

    business_name, website, score, contact, found_at

Verify before enabling anything:

    python manage.py check_leadscout

TIER A. This job reports leads to William. It does not contact anyone — that is
a TIER B draft, and it goes through the approval gate like everything else.
"""

import logging
from datetime import date

from django.conf import settings

log = logging.getLogger("core.agent")

DEFAULT_NEW_LEADS_SQL = """
    SELECT
        l.business_name  AS business_name,
        l.website        AS website,
        l.score          AS score,
        l.contact        AS contact,
        l.created_at     AS found_at
    FROM leads l
    WHERE l.created_at >= CURRENT_DATE - INTERVAL '1 day'
    ORDER BY l.score DESC
    LIMIT %(limit)s
"""


class LeadScoutUnavailable(Exception):
    """Not configured, or the database could not be reached."""


def is_configured():
    return bool(getattr(settings, "LEADSCOUT_DB_DSN", ""))


def _connect():
    dsn = getattr(settings, "LEADSCOUT_DB_DSN", "")
    if not dsn:
        raise LeadScoutUnavailable(
            "LEADSCOUT_DB_DSN is not set. Use a READ-ONLY database role."
        )
    try:
        import psycopg
        return psycopg.connect(dsn, connect_timeout=8)
    except ImportError:
        try:
            import psycopg2
            return psycopg2.connect(dsn, connect_timeout=8)
        except ImportError as exc:
            raise LeadScoutUnavailable("psycopg is not installed") from exc


def fetch_new_leads(limit=10):
    sql = getattr(settings, "LEADSCOUT_NEW_LEADS_SQL", "") or DEFAULT_NEW_LEADS_SQL

    if not sql.strip().lower().startswith("select"):
        raise LeadScoutUnavailable("configured SQL is not a SELECT — refusing to run it")

    rows = []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, {"limit": limit})
                columns = [c[0] for c in cur.description]
                for record in cur.fetchall():
                    rows.append(dict(zip(columns, record)))
    except LeadScoutUnavailable:
        raise
    except Exception as exc:
        raise LeadScoutUnavailable(f"LeadScout read failed: {exc}") from exc

    return rows
