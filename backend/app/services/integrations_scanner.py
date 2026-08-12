"""Auto-discovery of integrations/ → App + AppVersion rows.

The ``integrations/`` directory at the repo root holds one workspace per
first-party app HEAXHub ships out of the box (heax-demo-cli, heax-demo-streamlit,
...). Each directory contains a ``.portal/manifest.yaml`` that declares an id,
a version, a stack, and a launch mode.

This scanner is run twice:

* once on uvicorn startup (see ``app.main:lifespan``), so a fresh deployment
  has its registry populated before the first request lands;
* every 5 minutes from Celery beat (see
  :mod:`app.workers.integration_tasks`), so version bumps committed to disk
  pick up without a reboot.

It is deliberately conservative:

* Only ``upsert`` semantics — we never delete an App row.
* No actual builds are kicked off here; ``current_version_id`` is set so the
  app immediately appears in the catalogue, and service-mode apps get a
  best-effort ``service_manager.start_service`` call. Build/runtime failures
  must not block discovery of the next integration.

If the manifest file is malformed, the unknown stack name, or the seed admin is
missing, we log and skip — the scan loop must always finish.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import os

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logger import get_logger
from app.db.models.app import (
    App,
    AppStatus,
    AppType,
    AppVisibility,
    ExecutionTarget,
)
from app.db.models.app_version import AppVersion, BuildStatus
from app.db.models.user import User, UserRole
from app.services import audit_service, stack_resolver
from app.services.stack_resolver import StackSpec

logger = get_logger(__name__)


# Project root is two levels up from backend/app/services/.
# integrations/ sits at the same level as backend/.
_REPO_ROOT = Path(__file__).resolve().parents[3]
INTEGRATIONS_ROOT: Path = _REPO_ROOT / "integrations"


ScanAction = Literal["created", "updated", "unchanged", "skipped", "launch_failed"]


@dataclass(slots=True)
class ScanResult:
    """Single integration directory outcome."""

    slug: str
    action: ScanAction
    app_id: str | None = None
    version: str | None = None
    reason: str | None = None  # populated on "skipped"


@dataclass(slots=True)
class SourceSpec:
    """Parsed ``source:`` block from ``.portal/manifest.yaml``.

    Mirrors the subset of ``source_fetcher`` fields HEAXHub uses to fetch
    upstream code per-integration. When ``manifest.source`` is absent the
    workspace is the in-tree ``integrations/<slug>/`` directory and no
    SourceSpec is produced.
    """

    type: str = "git"
    url: str = ""
    ref: str = "main"
    subpath: str = ""

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> "SourceSpec | None":
        """Return a ``SourceSpec`` when the manifest carries a ``source`` block.

        Returns ``None`` if absent (legacy in-tree mode) or if the block is
        not a mapping. Other malformed values raise ``ValueError`` so the
        operator gets a loud, actionable error instead of a silently broken
        workspace.
        """
        if not isinstance(manifest, dict):
            return None
        block = manifest.get("source")
        if block is None:
            return None
        if not isinstance(block, dict):
            raise ValueError("manifest.source must be a mapping")

        stype = str(block.get("type") or "git").strip() or "git"
        url = str(block.get("url") or "").strip()
        ref = str(block.get("ref") or "main").strip() or "main"
        subpath = str(block.get("subpath") or "").strip()

        if stype == "git" and not url:
            raise ValueError("manifest.source.url is required for type=git")

        return cls(type=stype, url=url, ref=ref, subpath=subpath)


# ---------------------------------------------------------------------------
# Manifest + admin helpers
# ---------------------------------------------------------------------------


def _load_manifest(manifest_path: Path) -> dict[str, Any] | None:
    """Parse manifest.yaml. Returns None on any error (logs the cause)."""
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("manifest unreadable: %s (%s)", manifest_path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("manifest must be a mapping: %s", manifest_path)
        return None
    return raw


def _system_user(db: Session) -> User | None:
    """Resolve a sane ``owner_user_id`` for system-discovered apps.

    Prefers the seeded admin email (configured via ``SEED_ADMIN_EMAIL``);
    falls back to the first admin in the DB. Returns ``None`` if no admin
    exists — in that case the scanner will skip writes until one is created.
    """
    # Local import keeps this module test-friendly when settings is monkeypatched.
    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    seed_email = (settings.seed_admin_email or "").strip().lower()
    if seed_email:
        user = db.execute(
            select(User).where(User.email == seed_email)
        ).scalar_one_or_none()
        if user is not None:
            return user
    return db.execute(
        select(User).where(User.role == UserRole.ADMIN).order_by(User.created_at.asc())
    ).scalars().first()


def _coerce_app_type(value: str | None, default: AppType) -> AppType:
    if not value:
        return default
    try:
        return AppType(value)
    except ValueError:
        logger.warning("unknown app_type=%r, using %s", value, default.value)
        return default


def _coerce_execution_target(value: str | None, default: ExecutionTarget) -> ExecutionTarget:
    if not value:
        return default
    try:
        return ExecutionTarget(value)
    except ValueError:
        logger.warning(
            "unknown execution_target=%r, using %s", value, default.value
        )
        return default


def _resolve_visibility(manifest: dict[str, Any]) -> AppVisibility:
    """Read ``permissions.visibility`` from the manifest with a TEAM default."""
    perms = manifest.get("permissions") or {}
    raw = perms.get("visibility") if isinstance(perms, dict) else None
    if not raw:
        return AppVisibility.TEAM
    try:
        return AppVisibility(str(raw))
    except ValueError:
        logger.warning("unknown visibility=%r, using team", raw)
        return AppVisibility.TEAM


# ---------------------------------------------------------------------------
# Service-mode best-effort start
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BuildOutcome:
    """What ``_build_and_launch`` did so the caller can persist + report it.

    ``rebuilt`` is True only when a SIF/host build actually executed (cache
    miss). It stays False on cache hits, so an unchanged demo never gets
    re-reported as 'updated'.
    """

    status: BuildStatus
    rebuilt: bool = False
    sif_path: str | None = None
    build_log_path: str | None = None
    commit: str | None = None
    # 기동 실패는 빌드 상태와 별개다. 이게 없어서 앱이 안 떠도 요약이 'unchanged' 로 찍혔다.
    launch_failed: bool = False
    error: str | None = None


def _build_and_launch(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    stack: StackSpec,
    integration_dir: Path,
    manifest: dict[str, Any],
    commit_gated: bool = False,
) -> BuildOutcome:
    """Fetch source, build SIF (or fall back to legacy host-PATH builder),
    then launch. Records the build outcome on the AppVersion row.

    ``commit_gated`` (used on the same-version rescan path) narrows the rebuild
    trigger to a real upstream commit move: if the fetch reports the commit is
    unchanged and a built SIF already exists, the expensive SIF build is
    skipped even when cosmetic manifest fields drifted. This is the
    conservative trigger the operators asked for — a description/tag edit must
    not stampede a rebuild of every demo. New versions (``commit_gated=False``)
    always build.

    When manifest.source is present:
      1) integration_fetcher.fetch_for_integration clones the upstream into
         var/integration_workspaces/<slug>/upstream/
      2) integration_sif_builder.build_sif renders the stack .def template
         and invokes apt_runner.run_build → var/sifs/<slug>.sif (atomic swap)
      3) integration_launcher.launch is called with sif_path so it dispatches
         via apptainer instance start/exec.

    When manifest.source is absent the legacy in-tree builder + host-PATH
    launcher runs (backwards compatibility with pre-2.0 manifests).

    The AppVersion row is moved to BUILDING before the build and finalized to
    SUCCESS (+ sif_path/build_log_path/git_commit_hash) or FAILED
    (+ build_log_path) afterwards. All steps are best-effort: failures are
    logged + persisted, never raised, so discovery of the next integration
    continues. The returned :class:`BuildOutcome` lets the caller decide
    whether a same-version scan counts as 'updated' (rebuilt) or 'unchanged'.
    """
    slug = integration_dir.name

    # ── parse source block, if any ────────────────────────────────────
    try:
        source = SourceSpec.from_manifest(manifest)
    except ValueError as exc:
        logger.warning("source block invalid for %s: %s", app.id, exc)
        return _record_build(
            db, app=app, version=version, manifest=manifest,
            outcome=BuildOutcome(status=BuildStatus.FAILED, error=str(exc)),
        )

    # Mark BUILDING up-front so a crash mid-build leaves an honest status
    # behind (instead of the old hard-coded SUCCESS).
    if version.build_status != BuildStatus.BUILDING:
        version.build_status = BuildStatus.BUILDING
        db.commit()

    sif_path: str | None = None
    build_log_path: str | None = None
    commit: str | None = None
    rebuilt = False

    if source is not None and source.url:
        # ── fetch upstream into managed workspace ─────────────────────
        try:
            from app.services import integration_fetcher  # noqa: PLC0415
            fr = integration_fetcher.fetch_for_integration(slug, source)
            if fr.action == "failed":
                logger.warning("fetch failed for %s: %s", app.id, fr.error)
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        error=f"fetch failed: {fr.error}",
                    ),
                )
            commit = fr.commit
            if fr.action in {"cloned", "updated"}:
                logger.info("integration fetched: %s (%s) commit=%s",
                            app.id, fr.action, (fr.commit or "?")[:8])
        except Exception as exc:  # noqa: BLE001
            logger.exception("fetcher crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"fetcher crashed: {exc}"
                ),
            )

        # ── conservative gate on the rescan path ──────────────────────
        # When commit-gated, a fetch that didn't move the commit (action
        # "skipped") and an already-built SIF means there is nothing new to
        # build — skip straight to relaunch. This sidesteps build_sif's
        # whole-manifest hash, which is too eager (a description edit would
        # otherwise rebuild). We still backfill metadata so the row catches up.
        #
        # ⚠ "fetch 가 skipped" ≠ "빌드할 것 없음". fetch 는 지난 사이클에 이미 당긴
        # 커밋을 두고도 skipped 라 보고하므로, 그 사이클의 빌드가 실패/중단됐다면
        # SIF 는 낡은 채 이 게이트가 영구히 빌드를 막는다(실사례: WebDesignAgents
        # /mcp 커밋이 fetch 만 되고 SIF 에 못 들어감). 그래서 스킵은 "이 커밋으로
        # 빌드 성공 이력이 있다"(AppVersion.git_commit_hash 일치)일 때만 허용한다.
        # 커밋 정보가 없는 소스(비 git)는 종전대로 SIF 존재만으로 스킵한다.
        if commit_gated and fr.action == "skipped":
            existing_sif = _existing_sif_path(slug)
            built_commit = (version.git_commit_hash or "") if version else ""
            commit_ok = not commit or built_commit == commit[:64]
            if existing_sif is not None and commit_ok:
                logger.debug("commit unchanged for %s — skipping SIF build", app.id)
                outcome = _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.SUCCESS,
                        rebuilt=False,
                        sif_path=str(existing_sif),
                        # ⚠ commit 은 여기서 기록하지 않는다 — git_commit_hash 는
                        # "실제 빌드 성공" 의 증거로만 존재해야 위 commit_ok 판정이
                        # 성립한다. 스킵 경로가 이를 백필하면 빌드 없이 이력이
                        # 위조되어(실사례: WDA cc3370f) 고착 가드가 무력화된다.
                        commit=None,
                    ),
                )
                # 이 경로(커밋 불변 → SIF 재빌드 생략 → 곧장 재기동)가 안정 운영 중인
                # 앱이 실제로 타는 길이다. 여기서 실패를 안 올리면 요약이 계속 'unchanged'다.
                if _maybe_launch(
                    db, app=app, stack=stack, integration_dir=integration_dir,
                    manifest=manifest, source=source, sif_path=str(existing_sif),
                ) == "failed":
                    outcome.launch_failed = True
                return outcome

        # ── build per-demo SIF via apptainer ──────────────────────────
        try:
            from app.services import integration_sif_builder  # noqa: PLC0415
            sr = integration_sif_builder.build_sif(slug, manifest, fr)
            commit = sr.commit or commit
            if sr.log_path is not None:
                build_log_path = str(sr.log_path)
            if sr.action == "failed":
                logger.warning("SIF build failed for %s: %s", app.id,
                               (sr.error or "")[:300])
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        build_log_path=build_log_path,
                        commit=commit,
                        error=sr.error,
                    ),
                )
            if sr.action == "built":
                logger.info("SIF built: %s (%s)", app.id, sr.sif)
                rebuilt = True
            elif sr.action == "skipped":
                logger.debug("SIF skipped for %s (cached or no template)", app.id)
            sif_path = str(sr.sif) if sr.sif else None
        except Exception as exc:  # noqa: BLE001
            logger.exception("sif_builder crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"sif_builder crashed: {exc}"
                ),
            )
    else:
        # ── legacy host-PATH builder for in-tree integrations ─────────
        try:
            from app.services import integration_builder  # noqa: PLC0415
            br = integration_builder.build(integration_dir, manifest=manifest)
            if br.log_path:
                build_log_path = str(br.log_path)
            if br.action == "failed":
                logger.warning("build failed for %s: %s", app.id, br.error)
                return _record_build(
                    db, app=app, version=version, manifest=manifest,
                    outcome=BuildOutcome(
                        status=BuildStatus.FAILED,
                        build_log_path=build_log_path,
                        error=br.error,
                    ),
                )
            if br.action == "built":
                logger.info("integration built: %s (stack=%s, %.1fs)",
                            app.id, br.stack, br.duration_seconds)
                rebuilt = True
        except Exception as exc:  # noqa: BLE001
            logger.exception("builder crashed for %s: %s", app.id, exc)
            return _record_build(
                db, app=app, version=version, manifest=manifest,
                outcome=BuildOutcome(
                    status=BuildStatus.FAILED, error=f"builder crashed: {exc}"
                ),
            )

    # Build succeeded (or was a cache hit) → record SUCCESS before launching
    # so a launch crash doesn't leave the row stuck on BUILDING.
    outcome = _record_build(
        db, app=app, version=version, manifest=manifest,
        outcome=BuildOutcome(
            status=BuildStatus.SUCCESS,
            rebuilt=rebuilt,
            sif_path=sif_path,
            build_log_path=build_log_path,
            commit=commit,
        ),
    )

    if _maybe_launch(
        db, app=app, stack=stack, integration_dir=integration_dir,
        manifest=manifest, source=source, sif_path=sif_path,
    ) == "failed":
        outcome.launch_failed = True
    return outcome


def _existing_sif_path(slug: str) -> Path | None:
    """Return the built SIF for ``slug`` if it exists on disk, else None."""
    from app.services import integration_sif_builder  # noqa: PLC0415

    candidate = integration_sif_builder.SIF_DIR / f"{slug}.sif"
    return candidate if candidate.exists() else None


def _maybe_launch(
    db: Session,
    *,
    app: App,
    stack: StackSpec,
    integration_dir: Path,
    manifest: dict[str, Any],
    source: "SourceSpec | None",
    sif_path: str | None,
) -> str | None:
    """Launch service-mode integrations (best-effort, never raises).

    The launcher's own ``already_running`` liveness probe makes this idempotent
    for healthy services, so it is safe to call on every scan.

    런처의 action("started"/"already_running"/"failed"…)을 그대로 돌려준다 — 예전엔 None 만
    반환해서 호출부가 기동 실패를 알 방법이 없었고, 그래서 스캔 요약이 'unchanged' 로 찍혔다.
    """
    if stack.launch_mode != "service":
        return
    try:
        from app.services import integration_launcher  # noqa: PLC0415
        from dataclasses import asdict  # noqa: PLC0415
        lr = integration_launcher.launch(
            integration_dir, manifest=manifest, db=db,
            slug=integration_dir.name,
            source=asdict(source) if source else None,
            sif_path=Path(sif_path) if sif_path else None,
        )
        if lr.action == "failed":
            logger.warning("launch failed for %s: %s", app.id, lr.error)
            # 빌드 실패는 audit + 메일로 내구 기록이 남는데(_notify_build_failed) 기동 실패는
            # 이 WARNING 한 줄이 전부였다. 그래서 카탈로그 DB 는 stable/success 를 유지하고
            # 앱이 며칠째 죽어 있어도(실측: heax_demo_go 7/27~, nextjs 8/2~) 아무도 모른다.
            # 다만 이 경로는 45초(reconcile)·5분(scan) 주기로 돌므로 매번 남기면 audit 이
            # 폭주한다 — 같은 앱·같은 오류는 _LAUNCH_FAIL_COOLDOWN_S 동안 한 번만 남긴다.
            _audit_launch_failed(db, app=app, error=lr.error)
        elif lr.action == "started":
            logger.info("integration started: %s pid=%s port=%s",
                        app.id, lr.pid, lr.port)
        else:
            logger.debug("integration %s: %s", app.id, lr.action)
        if lr.action != "failed":
            # 카탈로그 버전 라벨 대조 — 실패해도 스캔은 계속(부가 진단이지 게이트가 아니다).
            try:
                _check_version_drift(db, app=app, manifest=manifest, lr=lr)
            except Exception as exc:  # noqa: BLE001
                logger.debug("version drift check skipped for %s: %r", app.id, exc)
        return getattr(lr, "action", None)
    except Exception as exc:  # noqa: BLE001
        logger.exception("launcher crashed for %s: %s", app.id, exc)
        return "failed"   # 런처가 예외로 죽은 것도 기동 실패다 — 요약에 드러나야 한다


_VERSION_KEYS = ("version", "app_version")


def _health_version(port: int | None, base_path: str, health_path: str) -> str | None:
    """앱이 헬스 응답에 노출한 **실제** 버전. 노출하지 않으면 None(대조 생략)."""
    if not port:
        return None
    import httpx  # noqa: PLC0415 — 런처와 동일하게 지연 import

    for url in (f"http://127.0.0.1:{port}{health_path}",
                f"http://127.0.0.1:{port}{base_path}{health_path}"):
        try:
            r = httpx.get(url, timeout=3.0, follow_redirects=False)
            if r.status_code >= 400:
                continue
            payload = r.json()
        except Exception:  # noqa: BLE001 — 미노출·비JSON·다운은 전부 '대조 불가'
            continue
        if not isinstance(payload, dict):
            continue
        for src in (payload, payload.get("server"), payload.get("data")):
            if isinstance(src, dict):
                for k in _VERSION_KEYS:
                    v = src.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
    return None


def _check_version_drift(db: Session, *, app: App, manifest: dict[str, Any], lr: Any) -> None:
    """manifest version 과 앱이 헬스에 노출한 실제 버전을 대조해 경고하고 흔적을 남긴다.

    카탈로그는 manifest 기준으로 버전을 표시한다. 그래서 앱만 새로 배포되고 manifest 를
    안 올리면 카탈로그가 조용히 구버전을 계속 보여준다 — 지금까지 사람이 매번 수동으로
    맞춰 왔다. 앱이 헬스에 버전을 노출하면 여기서 자동으로 잡아 경고하고,
    ``App.extra['version_drift']`` 에 남겨 UI/조회에서도 드러나게 한다.
    버전을 노출하지 않는 앱은 조용히 건너뛴다(선택 기능 — 강제하지 않는다).
    """
    declared = str(manifest.get("version") or "").strip()
    health_path = ((manifest.get("launch") or {}).get("health_check") or {}).get("path") or "/health"
    actual = _health_version(getattr(lr, "port", None), getattr(lr, "base_path", "") or "", health_path)
    if not declared or not actual:
        return
    extra = dict(app.extra or {})
    if actual == declared:
        if extra.pop("version_drift", None) is not None:
            app.extra = extra          # 드리프트 해소 — 표시 제거
            db.commit()
        return
    logger.warning(
        "version drift %s: manifest=%s / health=%s — 카탈로그가 구버전을 표시합니다"
        " (manifest 의 version 을 올리거나 앱 버전을 확인하세요)",
        app.id, declared, actual,
    )
    extra["version_drift"] = {"manifest": declared, "health": actual}
    app.extra = extra
    db.commit()


def _record_build(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    manifest: dict[str, Any],
    outcome: BuildOutcome,
) -> BuildOutcome:
    """Persist the build result onto the AppVersion row + notify on failure.

    Only writes columns we have real values for (never blanks an existing
    sif_path on a cache-hit success). Best-effort: a DB error here is logged,
    not raised.
    """
    try:
        version.build_status = outcome.status
        if outcome.sif_path:
            version.sif_path = outcome.sif_path
        if outcome.build_log_path:
            version.build_log_path = outcome.build_log_path
        if outcome.commit:
            version.git_commit_hash = outcome.commit[:64]
        db.commit()
        db.refresh(version)
    except Exception as exc:  # noqa: BLE001
        logger.exception("failed to persist build status for %s: %s", app.id, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    if outcome.status == BuildStatus.FAILED:
        _notify_build_failed(
            db, app=app, version=version, manifest=manifest,
            log_path=outcome.build_log_path, error=outcome.error,
        )
    return outcome


# 기동 실패 감사기록 쿨다운(초) — 같은 앱의 같은 오류를 이 간격 안에는 한 번만 남긴다.
_LAUNCH_FAIL_COOLDOWN_S = int(os.environ.get("HEAX_LAUNCH_FAIL_COOLDOWN", "3600"))
_LAUNCH_FAIL_SEEN: dict[str, tuple[float, str]] = {}


def _audit_launch_failed(db: Session, *, app: App, error: str | None) -> None:
    """기동 실패를 audit 에 남긴다 — 빌드 실패(_notify_build_failed)와 대칭.

    프로세스 재시작 시 한 번 더 남을 수 있으나, 없는 것보다 낫다(운영자가 볼 창구가
    worker.log grep 뿐이었다).
    """
    import time  # noqa: PLC0415

    key = app.id
    err = (error or "")[:1000]
    now = time.monotonic()
    prev = _LAUNCH_FAIL_SEEN.get(key)
    if prev and prev[1] == err and (now - prev[0]) < _LAUNCH_FAIL_COOLDOWN_S:
        return
    _LAUNCH_FAIL_SEEN[key] = (now, err)
    audit_service.safe_log(
        db,
        actor_user_id=None,
        action="integration.launch.failed",
        target_type="app",
        target_id=str(app.id),
        meta={"app_id": app.id, "error": err},
    )


def _notify_build_failed(
    db: Session,
    *,
    app: App,
    version: AppVersion,
    manifest: dict[str, Any],
    log_path: str | None,
    error: str | None,
) -> None:
    """Tell an operator a build failed: email if configured, else audit log.

    Always writes an audit ``integration.build.failed`` entry as the durable
    record; the email is a best-effort heads-up on top of it.
    """
    meta = {
        "app_id": app.id,
        "version": version.version,
        "log_path": log_path,
        "error": (error or "")[:1000],
    }
    audit_service.safe_log(
        db,
        actor_user_id=None,
        action="integration.build.failed",
        target_type="app_version",
        target_id=str(version.id),
        meta=meta,
    )

    # Best-effort operator email. Never let a mail failure escape.
    try:
        from app.config import get_settings  # noqa: PLC0415
        from app.services import mail_service  # noqa: PLC0415

        settings = get_settings()
        to = (settings.seed_admin_email or "").strip()
        if not to:
            return
        body = (
            f"Integration build FAILED.\n\n"
            f"app: {app.id}\nversion: {version.version}\n"
            f"log: {log_path or '(none)'}\n\n"
            f"--- error ---\n{(error or '(no detail)')[:2000]}\n"
        )
        mail_service.send_mail(
            to=to,
            subject=f"[HEAXHub] integration build failed: {app.id}@{version.version}",
            body=body,
        )
    except Exception:  # noqa: BLE001
        logger.exception("build-failed notification mail failed for %s", app.id)


# ---------------------------------------------------------------------------
# Single-dir processing
# ---------------------------------------------------------------------------


def _process_dir(
    db: Session, *, slug: str, integration_dir: Path, system_user_id: Any
) -> ScanResult:
    manifest_path = integration_dir / ".portal" / "manifest.yaml"
    manifest = _load_manifest(manifest_path)
    if manifest is None:
        return ScanResult(slug=slug, action="skipped", reason="manifest invalid")

    app_id = str(manifest.get("id") or "").strip()
    if not app_id:
        return ScanResult(slug=slug, action="skipped", reason="manifest.id missing")

    version_str = str(manifest.get("version") or "").strip() or "0.1.0"

    build_block = manifest.get("build") or {}
    stack_name = str(build_block.get("stack") or "").strip() if isinstance(build_block, dict) else ""
    if not stack_name:
        return ScanResult(
            slug=slug,
            action="skipped",
            app_id=app_id,
            reason="manifest.build.stack missing",
        )

    try:
        stack = stack_resolver.resolve(stack_name)
    except Exception as exc:  # noqa: BLE001 — NotFoundError, etc.
        return ScanResult(
            slug=slug,
            action="skipped",
            app_id=app_id,
            reason=f"unknown stack '{stack_name}': {exc}",
        )

    workspace_path = str(integration_dir)
    name = str(manifest.get("name") or app_id)
    description = manifest.get("description")
    tags = manifest.get("tags") or []
    visibility = _resolve_visibility(manifest)
    app_type = _coerce_app_type(manifest.get("app_type"), AppType(stack.app_type))
    execution_target = _coerce_execution_target(
        manifest.get("execution_target"), ExecutionTarget(stack.execution_target)
    )

    app = db.get(App, app_id)
    created_app = False
    if app is None:
        app = App(
            id=app_id,
            name=name,
            description=description,
            owner_user_id=system_user_id,
            app_type=app_type,
            execution_target=execution_target,
            status=AppStatus.STABLE,
            visibility=visibility,
            # No real upstream repo for these — they live in-tree. Use the
            # workspace path so source_fetcher / refresh tasks don't crash on
            # an empty URL.
            upstream_repo_url=f"file://{workspace_path}",
            tags=list(tags) if isinstance(tags, list) else [],
            workspace_path=workspace_path,
            extra={"stack": stack.name, "discovered": True},
        )
        db.add(app)
        db.flush()
        created_app = True
        logger.info("integrations scan: created app %s (stack=%s)", app_id, stack.name)
    else:
        # Keep workspace_path and visibility in sync with what's on disk.
        if app.workspace_path != workspace_path:
            app.workspace_path = workspace_path

        # name/description 은 예전엔 아예 안 건드렸다 — 관리자가 admin UI 로 바꿨을 수 있어서다.
        # 그 의도는 맞지만 부작용이 컸다: 매니페스트에서 이름을 바꿔도 이미 등록된 박스에는
        # 영원히 반영되지 않는다. 실제로 kooremapper→DynaForge 로 바꾼 뒤에도 cae00 UI 는
        # 계속 'KooRemapper' 로 떴다(dev 는 행이 새로 생성돼 우연히 맞았다).
        #
        # '사람이 바꾼 적 없으면 매니페스트를 따라간다' 로 좁힌다. 마지막으로 동기화한
        # 매니페스트 값을 extra 에 남겨 두고, 현재 DB 값이 그것과 같을 때만 갱신한다.
        # 다르면 관리자가 손댄 것이므로 그대로 둔다.
        # ⚠ 매니페스트가 '명시한' 값만 정본이다. name 은 없으면 app_id 로 폴백되므로
        # (715행 `manifest.get("name") or app_id`) 그걸 그대로 반영하면 'Demo · N(0,1)
        # Sampler (C++17)' 같은 멀쩡한 이름을 'heax_demo_cpp' 로 덮어쓴다(실측으로 겪음).
        # 매니페스트에 키가 있을 때만 동기화한다.
        _ex = dict(app.extra or {})
        _declared = {
            "name": str(manifest["name"]).strip() if manifest.get("name") else None,
            "description": description if manifest.get("description") else None,
        }
        for _field, _new in _declared.items():
            _cur = getattr(app, _field)
            _last = _ex.get(f"manifest_{_field}")
            # 마커가 없으면(이 코드 이전에 만들어진 행) 매니페스트를 정본으로 본다 —
            # 그러지 않으면 이름 변경이 영영 반영되지 않는다.
            if _new and _cur != _new and (_last is None or _cur == _last):
                setattr(app, _field, _new)
                logger.info("integrations scan: %s %s '%s' → '%s' (매니페스트 반영)",
                            app_id, _field, _cur, _new)
            _ex[f"manifest_{_field}"] = _new
        app.extra = _ex

    # Look for an existing version row with the same string version.
    existing_version = db.execute(
        select(AppVersion)
        .where(AppVersion.app_id == app_id)
        .where(AppVersion.version == version_str)
    ).scalars().first()

    if existing_version is None:
        version = AppVersion(
            app_id=app_id,
            version=version_str,
            manifest_snapshot=manifest,
            # Start PENDING; _build_and_launch flips it to BUILDING and then
            # SUCCESS/FAILED based on what actually happens. (Was hard-coded
            # SUCCESS before the build had even run.)
            build_status=BuildStatus.PENDING,
        )
        db.add(version)
        db.flush()
        app.current_version_id = version.id
        db.commit()
        db.refresh(app)
        db.refresh(version)
        _build_and_launch(
            db, app=app, version=version, stack=stack,
            integration_dir=integration_dir, manifest=manifest,
        )
        return ScanResult(
            slug=slug,
            action="created" if created_app else "updated",
            app_id=app_id,
            version=version_str,
        )

    # Same version already exists. Make sure current_version_id points at it
    # (it may have been cleared) and refresh the manifest snapshot in case
    # someone edited the YAML without bumping the version.
    if app.current_version_id != existing_version.id:
        app.current_version_id = existing_version.id
        db.commit()
        db.refresh(app)
    if existing_version.manifest_snapshot != manifest:
        existing_version.manifest_snapshot = manifest
        db.commit()
        db.refresh(existing_version)

    # Trigger expansion: re-run build/launch even when the version string is
    # unchanged, so a source push picks up without a manual version bump.
    # ``commit_gated=True`` keeps this conservative — a SIF rebuild fires only
    # when the upstream commit actually moves, never on cosmetic manifest edits
    # (description/tags). That avoids stampeding a rebuild of every demo. The
    # downstream content hashes (build_sif / integration_builder) still guard
    # the build itself, and the launcher's already_running probe makes the
    # relaunch idempotent for healthy services.
    outcome = _build_and_launch(
        db, app=app, version=existing_version, stack=stack,
        integration_dir=integration_dir, manifest=manifest,
        commit_gated=True,
    )

    if outcome.launch_failed:
        # 기동 실패가 'unchanged' 로 묻히면 5분마다 도는 요약 로그가 '전부 정상'으로 보인다.
        action: ScanAction = "launch_failed"
    elif created_app:
        action = "created"
    elif outcome.rebuilt:
        action = "updated"
    else:
        action = "unchanged"
    return ScanResult(
        slug=slug,
        action=action,
        app_id=app_id,
        version=version_str,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def scan_integrations(db: Session, *, root: Path | None = None) -> list[ScanResult]:
    """Walk ``integrations/`` and reconcile App/AppVersion rows.

    ``root`` overrides the discovered ``INTEGRATIONS_ROOT`` — handy for tests.
    """
    base = (root or INTEGRATIONS_ROOT).resolve()
    results: list[ScanResult] = []
    if not base.exists():
        logger.info("integrations root absent: %s — nothing to scan", base)
        return results

    sys_user = _system_user(db)
    if sys_user is None:
        logger.warning(
            "no admin user found — skipping integrations scan. Seed admin or "
            "set SEED_ADMIN_EMAIL to an existing admin.",
        )
        return results

    # Sort for deterministic ordering, primarily for tests.
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / ".portal" / "manifest.yaml"
        if not manifest.exists():
            continue
        try:
            results.append(
                _process_dir(
                    db,
                    slug=child.name,
                    integration_dir=child,
                    system_user_id=sys_user.id,
                )
            )
        except Exception as exc:  # noqa: BLE001 — never break the loop
            logger.exception("scan failed for %s", child)
            db.rollback()
            results.append(
                ScanResult(slug=child.name, action="skipped", reason=str(exc))
            )
    return results
