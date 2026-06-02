"""License provider abstraction — wraps either FlexLM lmstat or a mock.

Two concrete providers:

* :class:`FlexLMProvider` shells out to ``lmstat`` (path from
  ``settings.flexlm_lmstat_bin``) and parses ``Users of <feature>: (Total of N
  licenses issued; Total of M licenses in use)`` lines to compute availability.
* :class:`MockLicenseProvider` keeps an in-memory ``{feature: total}`` map
  (parsed from ``settings.mock_license_features``) and subtracts the count of
  currently-active rows in ``license_holdings`` to report availability.

The provider is consulted by :mod:`app.services.license_manager` *before* the
DB lock is taken so callers fail fast with a clear error when the external
feature pool is exhausted (or unreachable, for FlexLM).

Offline note: FlexLM is an on-prem binary; no PyPI wheels required. The mock
provider has zero external dependencies.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from sqlalchemy import func as sa_func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.logger import get_logger
from app.db.models.license_holding import LicenseHolding
from app.db.models.license_pool import LicensePool

logger = get_logger(__name__)


# Sentinel returned by check_available() when the provider could not reach its
# backend (e.g. lmstat binary missing). Callers should treat this as "unknown",
# distinct from a real zero.
UNKNOWN_AVAILABLE = -1


class BaseLicenseProvider:
    """Abstract interface — concrete providers report feature availability."""

    name: str = "base"

    def check_available(self, feature: str) -> int:
        """Return the number of tokens currently free for ``feature``.

        Returns :data:`UNKNOWN_AVAILABLE` when the backend cannot be reached.
        """
        raise NotImplementedError

    def health(self) -> dict:
        """Return a small JSON-friendly health snapshot."""
        raise NotImplementedError


# ─── FlexLM ──────────────────────────────────────────────────────────────────


# Matches "Users of lsdyna:  (Total of 8 licenses issued;  Total of 3 licenses in use)"
_FLEXLM_USERS_RE = re.compile(
    r"Users of (?P<feature>\S+):\s*\(Total of (?P<total>\d+) licenses? issued;"
    r"\s*Total of (?P<inuse>\d+) licenses? in use\)",
    re.IGNORECASE,
)


@dataclass
class _LmstatResult:
    total: int
    in_use: int

    @property
    def available(self) -> int:
        return max(0, self.total - self.in_use)


class FlexLMProvider(BaseLicenseProvider):
    """Shells out to ``lmstat -a -f <feature>``.

    The binary location is read from ``settings.flexlm_lmstat_bin``; if it does
    not exist on disk, :meth:`check_available` returns :data:`UNKNOWN_AVAILABLE`
    and :meth:`health` reports a ``degraded`` status. Operators should install
    FlexLM under ``/usr/local/flexlm/`` (or override the path) before flipping
    ``LICENSE_PROVIDER=flexlm``.
    """

    name = "flexlm"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    # -- internals -----------------------------------------------------------

    def _lmstat_bin(self) -> str:
        return self._settings.flexlm_lmstat_bin

    def _binary_exists(self) -> bool:
        path = self._lmstat_bin()
        if not path:
            return False
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return True
        # Fall back to PATH lookup (operator may have plain `lmstat` on PATH).
        return shutil.which(path) is not None

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        lic_file = (self._settings.flexlm_license_file or "").strip()
        if lic_file:
            env["LM_LICENSE_FILE"] = lic_file
        return env

    def _run_lmstat(self, feature: str) -> _LmstatResult | None:
        cmd = [self._lmstat_bin(), "-a", "-f", feature]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env=self._env(),
                timeout=10,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            logger.warning("lmstat invocation failed feature=%s err=%s", feature, exc)
            return None
        if proc.returncode != 0:
            logger.warning(
                "lmstat returncode=%s feature=%s stderr=%s",
                proc.returncode,
                feature,
                proc.stderr.strip()[:200],
            )
            # Still try to parse stdout — some FlexLM builds emit warnings yet
            # produce valid output.
        for line in proc.stdout.splitlines():
            m = _FLEXLM_USERS_RE.search(line)
            if m and m.group("feature").lower() == feature.lower():
                return _LmstatResult(
                    total=int(m.group("total")),
                    in_use=int(m.group("inuse")),
                )
        return None

    # -- BaseLicenseProvider -------------------------------------------------

    def check_available(self, feature: str) -> int:
        if not self._binary_exists():
            return UNKNOWN_AVAILABLE
        result = self._run_lmstat(feature)
        if result is None:
            return UNKNOWN_AVAILABLE
        return result.available

    def health(self) -> dict:
        bin_ok = self._binary_exists()
        return {
            "provider": self.name,
            "status": "ok" if bin_ok else "degraded",
            "lmstat_bin": self._lmstat_bin(),
            "lmstat_bin_present": bin_ok,
            "license_file": self._settings.flexlm_license_file or None,
        }


# ─── Mock ────────────────────────────────────────────────────────────────────


def _parse_mock_features(raw: str) -> dict[str, int]:
    """Parse ``"lsdyna:8,ansys:4"`` → ``{"lsdyna": 8, "ansys": 4}``."""
    out: dict[str, int] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        name, _, count = part.partition(":")
        name = name.strip()
        try:
            n = int(count.strip())
        except ValueError:
            continue
        if name and n >= 0:
            out[name] = n
    return out


class MockLicenseProvider(BaseLicenseProvider):
    """In-memory provider — totals come from settings, in-use from the DB.

    For each ``feature``, availability is ``total - sum(active holdings on
    pools matching that feature)``. Pools without an explicit ``feature`` are
    matched by ``LicensePool.name``.
    """

    name = "mock"

    def __init__(
        self, db: Session, settings: Settings | None = None
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._totals = _parse_mock_features(self._settings.mock_license_features)

    def _active_for_feature(self, feature: str) -> int:
        # Match pools by feature name (preferred) or fall back to pool name.
        pool_ids = list(
            self._db.execute(
                select(LicensePool.id).where(
                    (LicensePool.feature == feature) | (LicensePool.name == feature)
                )
            ).scalars()
        )
        if not pool_ids:
            return 0
        total = self._db.execute(
            select(sa_func.coalesce(sa_func.sum(LicenseHolding.tokens), 0))
            .where(LicenseHolding.pool_id.in_(pool_ids))
            .where(LicenseHolding.released_at.is_(None))
        ).scalar_one()
        return int(total or 0)

    def check_available(self, feature: str) -> int:
        if feature not in self._totals:
            # Unknown feature in mock mode → treat as unlimited so callers
            # aren't blocked when the env config hasn't been set up. We log
            # because this is almost always a misconfiguration in production.
            logger.info("mock provider: feature %s not configured, allowing", feature)
            return UNKNOWN_AVAILABLE
        used = self._active_for_feature(feature)
        return max(0, self._totals[feature] - used)

    def health(self) -> dict:
        return {
            "provider": self.name,
            "status": "ok",
            "features": dict(self._totals),
        }


# ─── Factory ─────────────────────────────────────────────────────────────────


def get_provider(db: Session, settings: Settings | None = None) -> BaseLicenseProvider:
    """Return the configured provider. ``mock`` needs a DB session; FlexLM does
    not, but we accept one for a uniform call-site signature."""
    s = settings or get_settings()
    if s.license_provider == "flexlm":
        return FlexLMProvider(settings=s)
    return MockLicenseProvider(db=db, settings=s)
