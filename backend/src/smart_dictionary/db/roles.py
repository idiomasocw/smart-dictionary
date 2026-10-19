"""The API's least-privilege database role (revision 0005)."""

import secrets
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

APP_ROLE = "dictionary_app"


def reset_password(conn: psycopg.Connection, admin_url: str, role: str = APP_ROLE) -> str:
    """Give the role a new random password and return the DATABASE_URL it connects with.

    The server only sees the SCRAM verifier, computed here, so the password can't land in
    a statement log."""
    password = secrets.token_urlsafe(32)
    verifier = conn.pgconn.encrypt_password(password.encode(), role.encode(), b"scram-sha-256")
    conn.execute(
        sql.SQL("alter role {} with login password {}").format(
            sql.Identifier(role), sql.Literal(verifier.decode())
        )
    )
    return app_url(admin_url, role, password)


def check_login(url: str, role: str, attempts: int = 5) -> None:
    """Log in with the URL, as the API will, and confirm the server sees the role. Supabase's
    pooler can take a moment to pick up a new password, so this tries a few times."""
    dsn = url.replace("postgresql+psycopg://", "postgresql://", 1)
    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(dsn, connect_timeout=10) as conn:
                row = conn.execute("select current_user").fetchone()
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)
            continue
        if row != (role,):
            raise RuntimeError(f"logged in as {row}, not {role}")
        return


def app_url(admin_url: str, role: str, password: str) -> str:
    """The admin URL with the role's credentials. Supabase's pooler names the project after
    a dot in the user name (postgres.<ref>), and the role keeps it (dictionary_app.<ref>)."""
    url = make_url(admin_url)
    _, dot, project = (url.username or "").partition(".")
    return url.set(username=role + dot + project, password=password).render_as_string(
        hide_password=False
    )
