"""Engine, session, and the tenant guard.

The guard is the point of this module. Multi-tenancy claimed in a README is a
promise; multi-tenancy enforced by the data layer is a property. Every
statement that touches a tenant-scoped table must mention `clinic_id`, or it
raises before it reaches Postgres.

That is deliberately cruder than row-level security and deliberately louder:
it fails in development, in tests, and in CI, on the developer who wrote the
query — not silently in production on a clinic whose client list leaked into
someone else's campaign.

See CLAUDE.md F-2.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from .models import GLOBAL_TABLES, TENANT_SCOPED_TABLES, Clinic


class TenantIsolationError(RuntimeError):
    """A statement touched tenant data without scoping it to a clinic."""


class ClinicNotFound(LookupError):
    """Raised instead of creating a clinic on a miss — see get_clinic()."""


# Statements that read or mutate existing rows. INSERT is exempt: it names
# clinic_id as a column when the model requires it, and the NOT NULL
# constraint is the real guarantee there.
_GUARDED = re.compile(r"^\s*(select|update|delete)\b", re.IGNORECASE)
_TENANT_TABLE = re.compile("|".join(rf"\b{t}\b" for t in TENANT_SCOPED_TABLES), re.IGNORECASE)


def _statement_is_safe(sql: str) -> bool:
    if not _GUARDED.match(sql):
        return True
    if not _TENANT_TABLE.search(sql):
        return True
    return "clinic_id" in sql.lower()


# The bypass lives in a ContextVar, NOT on Connection.info.
#
# SQLAlchemy keeps `Connection.info` on the pooled connection record, so it
# survives check-in. An earlier version stored the bypass there, and a single
# unguarded() call left the flag set on that pooled connection permanently —
# every later checkout of it ran unguarded. The guard silently stopped
# guarding, which is worse than never having had one. Caught by the first
# integration run against a real pool; unit tests could not see it.
#
# A ContextVar is scoped to the executing context and resets with the token,
# so the bypass cannot outlive its `with` block or leak into another task.
_BYPASS: ContextVar[bool] = ContextVar("relayops_tenant_bypass", default=False)


def install_tenant_guard(engine: Engine) -> None:
    """Reject any read/write of tenant data that is not clinic-scoped.

    Not installed during migrations: Alembic legitimately rewrites whole
    tables, and a schema migration has no tenant to scope to.
    """

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(conn, cursor, statement, parameters, context, executemany):
        if _BYPASS.get():
            return
        if not _statement_is_safe(statement):
            raise TenantIsolationError(
                "statement touches tenant-scoped data without a clinic_id predicate:\n"
                f"{statement.strip()[:400]}"
            )


def build_engine(url: str | None = None, *, guard: bool = True) -> Engine:
    settings = get_settings()
    engine = create_engine(url or settings.database_url, pool_pre_ping=True, future=True)
    if guard:
        install_tenant_guard(engine)
    return engine


def build_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def unguarded() -> Iterator[None]:
    """Escape hatch for the few legitimately cross-tenant reads.

    Only two callers should ever need this: the publisher enumerating active
    clinics, and operator views that aggregate across tenants. It is a context
    manager rather than a flag so the unscoped region is visible in the diff
    and cannot silently widen.

    Resets via the ContextVar token, so the bypass ends with the block even if
    the body raises — and never attaches to a pooled connection.
    """
    token = _BYPASS.set(True)
    try:
        yield
    finally:
        _BYPASS.reset(token)


def get_clinic(session: Session, name: str) -> Clinic:
    """Resolve a clinic by name. Raises on a miss — never creates.

    Creating on a miss means a typo in an import filename silently becomes a
    second tenant, splitting one clinic's clients across two scopes. The
    symptom appears weeks later as "half our clients stopped getting
    campaigns", and by then both scopes have real data in them.
    """
    with unguarded():
        clinic = session.query(Clinic).filter(Clinic.name == name).one_or_none()
    if clinic is None:
        raise ClinicNotFound(f"no clinic named {name!r}; register it first")
    return clinic


__all__ = [
    "GLOBAL_TABLES",
    "TENANT_SCOPED_TABLES",
    "ClinicNotFound",
    "TenantIsolationError",
    "build_engine",
    "build_sessionmaker",
    "get_clinic",
    "install_tenant_guard",
    "unguarded",
]
