"""Shared PostgreSQL connection config for Hawker Finder.

Reads the DB password from the environment (PG_PASSWORD) or /opt/data/.env so
credentials are never committed to git. Each script imports PG_DSN or PG_KWARGS.
"""
import os


def _read_env(name: str) -> str:
    """Read a key from the environment, falling back to /opt/data/.env."""
    val = os.environ.get(name, "")
    if val:
        return val
    for path in ("/opt/data/.env", os.path.join(os.path.dirname(__file__), ".env")):
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    if line.startswith(name + "="):
                        return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""


PG_HOST = os.environ.get("PG_HOST", "nas")
PG_PORT = int(os.environ.get("PG_PORT", "54321"))
PG_DB = os.environ.get("PG_DB", "hawker_finder")
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = _read_env("PG_PASSWORD")

# libpq DSN string (for psycopg2.connect("..."))
PG_DSN = f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} password={PG_PASSWORD}"

# Keyword-arg form (for psycopg2.connect(**DSN))
DSN = dict(host=PG_HOST, port=PG_PORT, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD)
