import os

import psycopg
from psycopg import errors
from dotenv import load_dotenv

load_dotenv(override=True)

IntegrityError = errors.IntegrityError


def _database_url():
    url = os.environ.get('DATABASE_URL', '').strip()
    if not url:
        raise RuntimeError(
            'DATABASE_URL is not set. Copy .env.example to .env and set your local Postgres URL.'
        )
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    return url


def get_connection():
    try:
        return psycopg.connect(_database_url())
    except psycopg.OperationalError as exc:
        url = _database_url()
        hint = (
            'Could not connect to PostgreSQL. '
            'Ensure Postgres is running (brew services start postgresql@16), '
            'the database exists (createdb farm_logger), and .env has the right DATABASE_URL. '
            f'Current URL: {url.split("@")[-1] if "@" in url else url}'
        )
        if 'role' in str(exc) and 'does not exist' in str(exc):
            hint += (
                ' If you see role "user", unset a stale shell variable: unset DATABASE_URL'
            )
        raise RuntimeError(hint) from exc
