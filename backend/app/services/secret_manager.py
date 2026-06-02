"""Secret manager — Fernet-encrypted environment variables with scope ladder.

Scopes:
  - `global`               default fallback for every app/job
  - `app:{app_id}`         per-app override
  - `user:{uuid}`          per-user override (defined for future use)

`inject_for_app` walks the ladder `app:{id}` -> `global` for every required key
and returns a plain dict ready to be merged into a subprocess env. Missing
required keys raise RuntimeError; the plaintext value is never logged.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.secret_value import SecretValue
from app.services import audit_service

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """True iff SECRET_ENCRYPTION_KEY is set in the environment."""
    return bool((get_settings().secret_encryption_key or "").strip())


def _fernet() -> Fernet:
    """Build a Fernet cipher from settings; raise if the key isn't configured."""
    key = get_settings().secret_encryption_key
    if not key:
        raise RuntimeError(
            "SECRET_ENCRYPTION_KEY not configured — run: "
            "python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())' and add to .env"
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def set_secret(
    db: Session,
    key: str,
    value: str,
    *,
    scope: str = "global",
    description: str | None = None,
    actor: uuid.UUID | None = None,
) -> SecretValue:
    """Create or rotate a secret. Plaintext value never reaches logs or DB."""
    cipher = _fernet()
    encrypted = cipher.encrypt(value.encode("utf-8"))

    row = db.execute(
        select(SecretValue).where(SecretValue.key == key, SecretValue.scope == scope)
    ).scalar_one_or_none()

    if row is None:
        row = SecretValue(
            key=key,
            scope=scope,
            value_encrypted=encrypted,
            description=description,
            created_by=actor,
        )
        db.add(row)
    else:
        row.value_encrypted = encrypted
        row.rotated_at = datetime.now(timezone.utc)
        if description is not None:
            row.description = description

    db.commit()
    db.refresh(row)
    logger.info("secret upserted scope=%s key=%s", scope, key)
    try:
        audit_service.safe_log(
            db,
            actor_user_id=actor,
            action="secret.set",
            target_type="secret",
            target_id=f"{scope}/{key}",
            meta={"scope": scope, "key": key},
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit hook failed for secret.set scope=%s key=%s", scope, key)
    return row


def get_secret(db: Session, key: str, *, scope: str = "global") -> str | None:
    """Return the plaintext value for (key, scope) or None if missing/corrupt."""
    row = db.execute(
        select(SecretValue).where(SecretValue.key == key, SecretValue.scope == scope)
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.value_encrypted).decode("utf-8")
    except InvalidToken:
        logger.error("secret decryption failed for scope=%s key=%s", scope, key)
        return None


def delete_secret(
    db: Session,
    key: str,
    *,
    scope: str = "global",
    actor: uuid.UUID | None = None,
) -> bool:
    """Remove a secret. Returns True if a row was deleted."""
    row = db.execute(
        select(SecretValue).where(SecretValue.key == key, SecretValue.scope == scope)
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("secret deleted scope=%s key=%s", scope, key)
    try:
        audit_service.safe_log(
            db,
            actor_user_id=actor,
            action="secret.delete",
            target_type="secret",
            target_id=f"{scope}/{key}",
            meta={"scope": scope, "key": key},
        )
    except Exception:  # noqa: BLE001
        logger.exception("audit hook failed for secret.delete scope=%s key=%s", scope, key)
    return True


def list_secrets(db: Session, *, scope_prefix: str | None = None) -> list[dict]:
    """Return metadata only (never plaintext) for UIs/admin listings."""
    stmt = select(SecretValue)
    if scope_prefix is not None:
        stmt = stmt.where(SecretValue.scope.like(f"{scope_prefix}%"))
    stmt = stmt.order_by(SecretValue.scope.asc(), SecretValue.key.asc())

    out: list[dict] = []
    for row in db.execute(stmt).scalars().all():
        out.append(
            {
                "id": str(row.id),
                "key": row.key,
                "scope": row.scope,
                "description": row.description,
                "created_at": row.created_at,
                "rotated_at": row.rotated_at,
            }
        )
    return out


def inject_for_app(db: Session, app_id: str, env_required: list[str]) -> dict[str, str]:
    """Resolve each required key via scope ladder `app:{id}` -> `global`.

    Raises RuntimeError listing every missing key (never logs the values).
    """
    if not env_required:
        return {}

    scopes = [f"app:{app_id}", "global"]
    stmt = select(SecretValue).where(
        SecretValue.scope.in_(scopes), SecretValue.key.in_(env_required)
    )
    # Build {scope: {key: row}} so we can prefer app-scope over global per key.
    by_scope: dict[str, dict[str, SecretValue]] = {s: {} for s in scopes}
    for row in db.execute(stmt).scalars().all():
        by_scope[row.scope][row.key] = row

    cipher = _fernet()
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for key in env_required:
        row = by_scope[f"app:{app_id}"].get(key) or by_scope["global"].get(key)
        if row is None:
            missing.append(key)
            continue
        try:
            resolved[key] = cipher.decrypt(row.value_encrypted).decode("utf-8")
        except InvalidToken:
            logger.error(
                "secret decryption failed for app=%s scope=%s key=%s", app_id, row.scope, key
            )
            missing.append(key)

    if missing:
        raise RuntimeError(
            f"missing required secrets for app '{app_id}': {', '.join(missing)}"
        )
    return resolved
