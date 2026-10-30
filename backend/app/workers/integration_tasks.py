"""Celery tasks for the integrations subsystem.

Two periodic passes run here (see ``celery_app.beat_schedule``):

* ``scan_integrations_periodic`` — discovery + build. Walks ``integrations/``,
  upserts App/AppVersion rows, and on a NEW version string builds + launches.
  The startup path in ``app.main:lifespan`` calls the scanner synchronously so a
  fresh boot has its registry populated before the first request.

* ``reconcile_integrations`` — self-heal. Caddy routes live only in the admin
  API's in-memory config, so a Caddy restart wipes every ``/apps/<id>`` route
  even while the upstream service is still alive. The scanner does NOT fix this:
  it early-returns on the unchanged-version path and never re-invokes
  ``launch()``. This reconcile pass re-invokes ``launch()`` for every
  service/static/proxy-mode integration on a short interval. ``launch()`` is
  idempotent — when the instance is alive + healthy and the route is present it
  re-PUTs the (idempotent) Caddy route and returns ``already_running``; when the
  instance is dead it cold-starts it (this is the P0-1 instance auto-recovery).
  No builds are triggered here — that is the scanner's job exclusively.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.logger import get_logger
from app.db.session import SessionLocal
from app.services import integration_manifest, integrations_scanner
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

# Same path rules the scanner / sif_builder use so reconcile resolves the exact
# same artifacts without re-deriving them through the build pipeline.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_INTEGRATIONS_ROOT = _REPO_ROOT / "integrations"
_SIF_DIR = _REPO_ROOT / "var" / "sifs"

# launch() handles these by registering a route (or cold-starting a process).
# Everything else (url/iframe/job-runner) has nothing for reconcile to restore.
_RECONCILE_MODES = frozenset({"service", "static", "proxy"})
# 라우트를 걸지 않는다고 '명시한' 모드 — config/stacks.yaml 의 launch_mode 가운데 위 셋을 뺀 것이다(시험이 둘을 견준다).
# 이 모드로 바뀐 앱만 남은 서비스를 내린다(HH-16). 모드가 없거나 모르는 값이면 내리지 않는다(HH-16 검토).
_UNROUTED_MODES = frozenset({"url", "iframe", "job_runner", "installer"})


@celery_app.task(name="integration_tasks.scan_integrations_periodic")
def scan_integrations_periodic() -> dict[str, object]:
    """Run the integration discovery pass and return a summary dict."""
    with SessionLocal() as db:
        results = integrations_scanner.scan_integrations(db)

    summary: dict[str, int] = {}
    for r in results:
        summary[r.action] = summary.get(r.action, 0) + 1
    logger.info("integrations scan: %s", summary)
    return {
        "count": len(results),
        "by_action": summary,
        "items": [
            {
                "slug": r.slug,
                "action": r.action,
                "app_id": r.app_id,
                "version": r.version,
                "reason": r.reason,
            }
            for r in results
        ],
    }


def reconcile_integrations() -> dict[str, object]:
    """Re-register routes + restart dead instances for live integrations.

    Idempotent and build-free. For every ``integrations/<slug>`` whose manifest
    launch mode is service/static/proxy, re-invoke ``integration_launcher.launch``
    with the SAME path rules the scanner uses (workspace = the integration dir,
    sif = ``var/sifs/<slug>.sif`` when present). ``launch`` itself decides
    whether that is a no-op (already_running) or a recovery (started/failed).

    Returns a summary dict counting results by ``launch`` action so the caller
    (celery task / admin endpoint / startup) can surface what happened.

    효과 매니페스트가 더는 service 가 아닌 앱이 허브가 띄운 서비스(heax_app_<id>)를 남겨 두었으면 이 패스가 내린다(HH-16) —
    proxy · static 은 launch() 가 새 라우트를 건 뒤, 그 밖의 모드는 아래 retire_unrouted 가('retired' 로 센다).
    """
    # Lazy import — integration_launcher pulls in apt_runner/httpx and we want
    # this module importable by celery_app's include list without that weight
    # until the task actually runs.
    from app.services import integration_launcher  # noqa: PLC0415

    by_action: dict[str, int] = {}
    items: list[dict[str, Any]] = []

    if not _INTEGRATIONS_ROOT.exists():
        logger.info("reconcile: integrations root absent: %s", _INTEGRATIONS_ROOT)
        return {"count": 0, "by_action": by_action, "items": items}

    with SessionLocal() as db:
        for child in sorted(p for p in _INTEGRATIONS_ROOT.iterdir() if p.is_dir()):
            manifest_path = child / ".portal" / "manifest.yaml"
            if not manifest_path.exists():
                continue
            # 앱 하나가 무엇을 내든 패스를 끊지 않는다 — 예전엔 launch 를 글자 하나로 적은 매니페스트 하나가 .get 에서
            # AttributeError 를 내, 정렬상 그 뒤의 앱 전부가 라우트 재등록 · 죽은 인스턴스 재기동을 45초마다 놓쳤다(HH-15 검토).
            try:
                item = _reconcile_one(integration_launcher, db, child, manifest_path)
            except Exception as exc:  # noqa: BLE001 — never break the loop
                logger.exception("reconcile: %s crashed", child.name)
                item = {"slug": child.name, "action": "failed", "error": str(exc)}
            if item is not None:
                by_action[item["action"]] = by_action.get(item["action"], 0) + 1
                items.append(item)

    logger.info("reconcile integrations: %s", by_action)
    return {"count": len(items), "by_action": by_action, "items": items}


def _reconcile_one(integration_launcher, db, child: Path, manifest_path: Path) -> dict[str, Any] | None:
    """앱 하나를 맞춘다 — 요약에 넣을 항목을, 할 일이 없었으면 None 을 돌려준다."""
    slug = child.name
    try:
        # 박스 전용 manifest.local.yaml 을 얹은 효과 매니페스트(HH-15) — 로컬 파일을 더하거나 고치거나 지우면
        # 이 패스에서 라우트 · 기동 판정이 그것을 따른다.
        manifest = integration_manifest.load_file(manifest_path) or {}
    except Exception as exc:  # noqa: BLE001 — never break the loop
        logger.warning("reconcile: manifest load failed for %s: %s", slug, exc)
        return None
    if not isinstance(manifest, dict):
        return None

    # launch 가 매핑이 아니거나 mode 가 글자가 아니면 모드가 없는 것으로 친다(아래에서 알리기만 한다).
    launch_section = manifest.get("launch")
    mode = launch_section.get("mode") if isinstance(launch_section, dict) else None
    if not isinstance(mode, str):
        mode = None
    if mode not in _RECONCILE_MODES:
        # HH-16 — 라우트를 걸지 않는 모드(url · iframe · job_runner …)로 바뀐 앱이 허브가 띄운 서비스를 남겨 두었으면
        # 내린다. 남은 것이 없으면 아무것도 하지 않아 요약도 예전과 같다. proxy · static 은 launch() 가 새 라우트를 건 뒤 내린다.
        # 모드가 없거나 모르는 값(오타 · launch 블록 삭제 · 파일을 다시 쓰는 순간의 빈 읽기)이면 내리지 않고 알리기만 한다.
        canonical = manifest.get("id") or slug.replace("-", "_")
        try:
            if mode not in _UNROUTED_MODES:
                integration_launcher.warn_stale_service(
                    canonical, f"launch.mode 가 {mode!r} 다(없거나 모르는 값)")
            elif integration_launcher.retire_unrouted(canonical, db=db):
                return {"slug": slug, "action": "retired", "port": None, "base_path": None, "error": None}
        except Exception:  # noqa: BLE001 — never break the loop
            logger.exception("reconcile: stale service retire crashed for %s", slug)
        return None

    sif_path = _SIF_DIR / f"{slug}.sif"
    sif_arg = sif_path if sif_path.exists() else None

    # Pass the manifest's source: block through so launch() (static mode
    # in particular) resolves artefacts under the fetched upstream
    # workspace (var/integration_workspaces/<slug>/upstream) rather than
    # the manifest-only integrations/<slug> dir, which has no build
    # output. Without this, static_html-style demos 404 on reconcile.
    source_block = (
        manifest.get("source")
        if isinstance(manifest.get("source"), dict)
        else None
    )

    try:
        lr = integration_launcher.launch(
            child, manifest=manifest, db=db, slug=slug,
            source=source_block, sif_path=sif_arg,
        )
    except Exception as exc:  # noqa: BLE001 — launch is best-effort
        logger.exception("reconcile: launch crashed for %s", slug)
        return {"slug": slug, "action": "failed", "error": str(exc)}

    if lr.action == "started":
        logger.info("reconcile: recovered %s port=%s pid=%s", slug, lr.port, lr.pid)
    elif lr.action == "failed":
        logger.warning("reconcile: %s failed: %s", slug, lr.error)
    return {
        "slug": slug,
        "action": lr.action,
        "port": lr.port,
        "base_path": lr.base_path,
        "error": lr.error,
    }


@celery_app.task(name="integration_tasks.reconcile_integrations")
def reconcile_integrations_periodic() -> dict[str, object]:
    """Beat-scheduled wrapper around :func:`reconcile_integrations`."""
    return reconcile_integrations()
