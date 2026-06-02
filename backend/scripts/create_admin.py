"""Seed an initial admin user from SEED_ADMIN_* env vars (idempotent).

Run with:  python -m scripts.create_admin
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a script from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.logger import get_logger  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models.user import AuthSource, User, UserRole, UserStatus  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

logger = get_logger("create_admin")


def main() -> int:
    settings = get_settings()
    email = settings.seed_admin_email.lower()

    with SessionLocal() as db:
        existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            if existing.role != UserRole.ADMIN:
                existing.role = UserRole.ADMIN
                db.commit()
                logger.info("Existing user %s promoted to ADMIN", email)
            else:
                logger.info("Admin user %s already exists, nothing to do", email)
            return 0

        admin = User(
            email=email,
            display_name=settings.seed_admin_name,
            organization=settings.seed_admin_org,
            password_hash=hash_password(settings.seed_admin_password),
            auth_source=AuthSource.LOCAL,
            status=UserStatus.ACTIVE,
            role=UserRole.ADMIN,
            email_verified=True,
        )
        db.add(admin)
        db.commit()
        logger.info("Created admin user %s", email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
