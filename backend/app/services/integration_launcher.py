"""Launch service-mode integrations as long-running host processes.

For each ``launch.mode == "service"`` integration the scanner picks up, this
module:

1. Allocates (or reuses) a port via :mod:`port_allocator`.
2. Spawns the service via ``setsid nohup`` so it survives this worker's exit.
3. Registers a Caddy route ``/apps/{slug}/*`` → ``127.0.0.1:<port>`` via the
   admin API (see :mod:`proxy_manager`).
4. Records the PID + port in a tiny on-disk state file at
   ``var/integration_state/{slug}.json`` so a restart can probe liveness and
   avoid double-spawning.

It is best-effort: failures log + return without raising. Job-runner mode
integrations are no-ops here (they spawn per-job, not as long-running daemons).

State file robustness
---------------------
* All writes are fsync'd + atomically renamed so power loss doesn't leave a
  half-written JSON.
* Every record carries a ``schema_version`` so older state from a previous
  HEAXHub release degrades gracefully (we ignore unknown versions instead of
  blowing up the launcher).
* Before sending SIGTERM we verify ``/proc/<pid>/cmdline`` still matches the
  argv we recorded — guards against the classic PID-reuse footgun where the
  stored pid has been recycled by an unrelated process.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.core.logger import get_logger
from app.services import apt_runner, integration_manifest, port_allocator, proxy_manager
from app.services.stack_resolver import StackSpec, load_stacks

logger = get_logger(__name__)


_REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR: Path = _REPO_ROOT / "var" / "integration_state"
LOG_DIR: Path = _REPO_ROOT / "var" / "logs"
# 앱별 영구 데이터 루트. var/ 는 gitignore 된 런타임 영역이라 소스 재클론·git clean·
# upstream 재fetch(rmtree)·SIF 재빌드 어디에도 휩쓸리지 않는다. 앱이 쓰기 상태
# (SQLite/업로드 등)를 여기 두면 재스캔·재빌드 뒤에도 남는다.
APP_DATA_ROOT: Path = _REPO_ROOT / "var" / "app_data"
STATE_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _host_has_nvidia() -> bool:
    """호스트에 NVIDIA GPU 가 붙어 있는지(그래서 ``--nv`` 를 켜도 되는지) 판정한다.

    ``resources.gpu: true`` 매니페스트라도 GPU 없는 호스트에서 ``apptainer instance
    start --nv`` 를 하면 "no nv files" 로 경고·실패한다. 그래서 여기서 디바이스
    노드(``/dev/nvidiactl`` 또는 ``/dev/nvidia0``) 존재로 게이트해, 없으면 CPU 로
    조용히 폴백한다(앱의 torch 는 CUDA 미탐지 → CPU 로 돈다).
    """
    return Path("/dev/nvidiactl").exists() or Path("/dev/nvidia0").exists()


def _app_data_dir(canonical: str) -> Path:
    """``var/app_data/<canonical>/`` — 앱 전용 영구 쓰기 디렉터리(생성 후 반환).

    SIF 경로에선 컨테이너 ``/data`` 로 bind, 호스트 경로에선 이 절대경로 자체를
    쓴다. 어느 쪽이든 앱은 주입된 ``HEAX_DATA_DIR`` 로 위치를 발견한다.

    ``canonical`` 은 매니페스트 id 에서 온다 — 파일시스템 경로로 쓰기 전에
    데이터 루트 밖으로 새지 않는지(traversal) 봉쇄한다.
    """
    root = APP_DATA_ROOT.resolve()
    d = (root / canonical).resolve()
    if d == root or not d.is_relative_to(root):
        raise ValueError(f"unsafe app data dir for canonical={canonical!r}")
    d.mkdir(parents=True, exist_ok=True)
    return d

_HEALTH_TIMEOUT = 2.0
_HEALTH_WAIT_SECONDS = 20

# Bump this when the state file shape changes incompatibly. Older state files
# (different or absent version) are ignored, NOT crashed on, so an upgrade
# in-place just costs one extra spawn cycle.
#
# v2 added optional ``sif_path`` + ``instance_name`` fields so the stop path
# can route to ``apt_runner.instance_stop`` instead of SIGTERM-ing a host pid.
_STATE_SCHEMA_VERSION = 2

# Stacks whose framework bakes the base path into routing and therefore must
# RECEIVE the full incoming path (no Caddy prefix strip). For everything else
# the upstream listens at "/" and Caddy strips ``/apps/<slug>`` before proxying,
# which is the safer default — most WSGI/HTTP servers (Flask, gunicorn, node
# express, Go net/http) don't auto-handle a sub-path on request URLs.
_PREFIX_AWARE_STACKS = frozenset({
    "streamlit", "nextjs", "node_service",
    # Dash builds internal links from requests_pathname_prefix; it must see the
    # full /apps/<slug>/... path, so do not strip at Caddy.
    "dash_plotly",
    # Shiny --root-path == ASGI root_path; upstream expects full prefix.
    "shiny_for_python",
    # Flask via DispatcherMiddleware requires SCRIPT_NAME == prefix AND the
    # request path to START with that prefix; Caddy strip breaks the second
    # half of that contract, so we forward unchanged.
    "flask",
})


@dataclass(slots=True)
class LaunchResult:
    slug: str
    action: str  # "skipped" | "started" | "already_running" | "failed" | "backoff"(앞선 실패로 다음 시도를 늦추는 중) | "busy"(다른 워커가 기동 중)
    port: int | None
    base_path: str | None
    pid: int | None = None
    error: str | None = None


# ── 되풀이 기동 실패 억제(10-17 cae00 리뷰 X4) ─────────────────────────────────────
# reconcile(45초) · scan(5분)은 서비스 앱마다 launch() 를 다시 부른다. 늘 실패하는 앱(cae00 heax_demo_nextjs — corepack 이
# 사내 인증서로 pnpm 을 못 받는다)은 그때마다 새로 띄워져 앱 출력과 'launch failed' 를 로그에 쌓았다(09-22~ 48,600여 회 · 392MB).
# 실패하면 다음 시도를 60초부터 두 배씩 늦춘다(상한 1시간). 매니페스트 · SIF 가 바뀌거나, 성공하거나, 운영자가 stop() 하면
# (redeploy-app.sh) 지운다. 기록은 상태 파일(<앱>.json — '한 번이라도 뜬 앱' 판정, mcp._ever_launched)과 다른 이름이다.
_BACKOFF_BASE_S = 60
_BACKOFF_MAX_S = 3600


def _backoff_path(canonical: str) -> Path:
    return STATE_DIR / f"{canonical}.launch-backoff"


def _launch_fingerprint(manifest: dict[str, Any], sif_path: Path | None) -> str:
    """무엇이 바뀌면 곧바로 다시 시도하나 — 매니페스트 내용과 SIF(크기 · 시각)."""
    h = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode("utf-8"))
    if sif_path is not None:
        try:
            st = sif_path.stat()
            h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        except OSError:
            h.update(b"no-sif")
    return h.hexdigest()[:16]


def _read_backoff(canonical: str) -> dict | None:
    try:
        d = json.loads(_backoff_path(canonical).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _note_launch_failure(canonical: str, fp: str, error: str | None) -> tuple[int, int]:
    """실패를 세고 다음 시도까지의 초를 정한다. → (몇 번째 실패, 기다릴 초)."""
    prev = _read_backoff(canonical)
    n = int(prev.get("failures", 0)) + 1 if prev and prev.get("fp") == fp else 1
    wait = min(_BACKOFF_BASE_S * 2 ** (n - 1), _BACKOFF_MAX_S)
    try:
        _backoff_path(canonical).write_text(json.dumps({
            "fp": fp, "failures": n, "next_at": time.time() + wait, "error": (error or "")[:500],
        }), encoding="utf-8")
    except OSError:  # 기록을 못 해도 기동 판정은 그대로 — 다음 호출이 다시 시도할 뿐이다
        pass
    try:
        with (LOG_DIR / f"integration_{canonical}.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 기동 실패 {n}번째 — 다음 시도 {wait}초 뒤(매니페스트 · SIF 가 바뀌면 "
                     f"곧바로). 지금 다시 하려면 redeploy-app.sh 또는 {_backoff_path(canonical)} 를 지운다\n")
    except OSError:
        pass
    return n, wait


def _clear_backoff(canonical: str) -> None:
    try:
        _backoff_path(canonical).unlink()
    except OSError:
        pass


def launch(
    workspace: Path,
    *,
    manifest: dict[str, Any],
    db,
    slug: str | None = None,
    source: dict[str, Any] | None = None,
    sif_path: Path | None = None,
) -> LaunchResult:
    """Ensure a healthy service is running for this integration — 서비스 앱의 되풀이 실패는 늦춰서 다시 한다(위 X4).

    나머지는 :func:`_launch_once` 그대로다. 앞선 실패로 기다리는 중이면 띄우지 않고 ``action="backoff"`` 를 돌려준다 — 단, 상태 파일의
    프로세스가 살아 있으면 막지 않는다(살아 있는지 · 건강한지는 아래가 판정한다).
    """
    if (manifest.get("launch") or {}).get("mode") != "service":
        return _launch_once(workspace, manifest=manifest, db=db, slug=slug, source=source, sif_path=sif_path)
    name = slug or workspace.name
    canonical = manifest.get("id") or name.replace("-", "_")
    fp = _launch_fingerprint(manifest, sif_path)
    rec = _read_backoff(canonical)
    if rec and rec.get("fp") == fp:
        wait = float(rec.get("next_at", 0)) - time.time()
        state = _read_state(canonical)
        if wait > 0 and not (state and _is_alive(state.get("pid"))):
            return LaunchResult(
                slug=name, action="backoff", port=None, base_path=f"/apps/{canonical}",
                error=f"기동 실패 {rec.get('failures')}번 — 다음 시도 {int(wait)}초 뒤(마지막 오류: {rec.get('error')})",
            )
    with _slug_launch_lock(canonical) as acquired:
        if not acquired:
            return LaunchResult(
                slug=name, action="busy", port=None, base_path=f"/apps/{canonical}",
                error="다른 워커가 이미 이 앱을 기동 중",
            )
        lr = _launch_once(workspace, manifest=manifest, db=db, slug=slug, source=source, sif_path=sif_path)
    if lr.action == "failed":
        _note_launch_failure(canonical, fp, lr.error)
    elif lr.action in ("started", "already_running"):
        _clear_backoff(canonical)
    return lr


@contextmanager
def _slug_launch_lock(canonical: str) -> Iterator[bool]:
    """앱별 기동 잠금(flock, 비차단) — reconcile(45초)·scan(5분) 이 같은 앱을 동시에 기동하는
    경로를 막는다(HH-12). 628400d 가 SIF 빌드에 쓴 것과 같은 패턴. 잡으면 True, 다른 프로세스가
    쥐고 있으면 False. 쥔 프로세스가 죽으면 flock 이 저절로 풀려 고착되지 않는다.
    """
    lock_path = STATE_DIR / f"{canonical}.launch.lock"
    try:
        fh = lock_path.open("a")
    except OSError as exc:
        logger.warning("launch: lock unavailable for %s (%s) — launching without lock", canonical, exc)
        yield True
        return
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("launch: already in progress for %s — busy", canonical)
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def _launch_once(
    workspace: Path,
    *,
    manifest: dict[str, Any],
    db,
    slug: str | None = None,
    source: dict[str, Any] | None = None,
    sif_path: Path | None = None,
) -> LaunchResult:
    """Ensure a healthy service is running for this integration.

    The ``db`` argument is the SQLAlchemy session used for port allocation.

    When ``sif_path`` is set and points to an existing SIF file, the launcher
    dispatches via ``apt_runner`` (apptainer instance) instead of spawning the
    server straight on the host. ``slug`` lets the caller override the default
    (workspace dirname) which is useful when the workspace was synthesised
    from a fetched source tree. ``source`` is the manifest's ``source:`` block
    if present — currently unused inside ``launch`` but kept on the signature
    so callers can pass it without breaking when we start binding it inside
    the container.
    """
    # ``source`` is captured for forward-compat; touch it to silence linters
    # without changing behaviour.
    _ = source
    slug = slug or workspace.name
    canonical = manifest.get("id") or slug.replace("-", "_")
    launch_section = manifest.get("launch") or {}
    mode = launch_section.get("mode")
    # HH-16 — 라우트를 거는 다른 모드(proxy · static)로 바뀐 앱이면, 아래 갈래가 상태 파일을 덮기 전에 허브가 예전에 이 id 로
    # 띄운 서비스 기록을 집어 둔다. 새 라우트가 선 뒤에 내린다(반대 순서면 그 사이 요청이 죽어 가는 인스턴스로 간다).
    stale = _stale_service_record(_read_state(canonical)) if mode in ("proxy", "static") else None

    # Non-process launch modes: bail out before any port allocation or Popen.
    # ``url`` and ``iframe`` are pure-frontend dispatches — the catalog reads
    # ``launch.url`` and either opens a new tab or embeds an iframe. There is
    # nothing for the launcher to spawn or register.
    if mode in ("url", "iframe"):
        return LaunchResult(
            slug=slug, action="skipped", port=None,
            base_path=f"/apps/{canonical}",
        )

    # ``proxy`` mode is non-process too, but we DO register a Caddy route
    # pointing /apps/{id}/* at an external upstream URL.
    if mode == "proxy":
        upstream = launch_section.get("upstream") or launch_section.get("url")
        if not upstream:
            return LaunchResult(
                slug=slug, action="failed", port=None,
                base_path=f"/apps/{canonical}",
                error="proxy mode requires launch.upstream (URL)",
            )
        base_path = f"/apps/{canonical}"
        strip_prefix = bool(launch_section.get("strip_prefix", True))
        # launch.portal_auth: true — 포탈 forward_auth 게이트 + X-Heax-* identity
        # 전파 + 게이트웨이 시크릿 주입 (업스트림 앱 SSO 자동 로그인용).
        portal_auth = bool(launch_section.get("portal_auth", False))
        try:
            res = proxy_manager.register_external_proxy_route(
                app_id=canonical,
                upstream_url=str(upstream),
                base_path=base_path,
                strip_prefix=strip_prefix,
                portal_auth=portal_auth,
            )
        except Exception as exc:
            return LaunchResult(
                slug=slug, action="failed", port=None, base_path=base_path,
                error=f"caddy register failed: {exc}",
            )
        if not getattr(res, "ok", False):
            return LaunchResult(
                slug=slug, action="failed", port=None, base_path=base_path,
                error=f"caddy register failed: {getattr(res, 'reason', 'unknown')}",
            )
        if stale and not _retire_stale_service(canonical, db=db):
            # 못 내렸다 — 서비스 기록이 든 상태 파일을 그대로 두어 다음 reconcile 이 다시 내리게 한다(라우트는 이미 새것이다).
            return LaunchResult(
                slug=slug, action="started", port=None, base_path=base_path,
            )
        # 기동 이력(state)을 남긴다. HEAX 가 프로세스를 띄우지 않는 proxy 라도, MCP 레지스트리의
        # _ever_launched 게이트(state 파일 유무)를 통과해야 /api/v1/mcp/servers 에 노출된다 —
        # 없으면 mcp.expose:true 를 선언해도 외부 연계 앱이 게이트웨이에서 영원히 안 보인다.
        # 프로세스가 없으므로 pid/port 는 None, 실체는 외부 upstream 이 담당한다.
        _write_state(canonical, {
            "schema_version": _STATE_SCHEMA_VERSION,
            "slug": canonical,
            "pid": None,
            "port": None,
            "base_path": base_path,
            "mode": "proxy",
            "upstream": str(upstream),
            "caddy_registered": True,
        })
        return LaunchResult(
            slug=slug, action="started", port=None, base_path=base_path,
        )

    # ``static`` mode: no process. Caddy serves the workspace's pre-built
    # static_root directory via the file_server handler. We resolve the root
    # to an absolute filesystem path Caddy can read, then register the route.
    if mode == "static":
        res = _launch_static(
            workspace, canonical, manifest,
            slug=slug, source=source,
        )
        # static 은 상태 파일을 쓰지 않는다 — 새 라우트가 섰으면 남은 서비스를 내리고 그 기록도 지운다(HH-16).
        if stale and res.action == "started" and _retire_stale_service(canonical, db=db):
            _delete_state(canonical)
        return res

    if mode != "service":
        return LaunchResult(
            slug=slug, action="skipped", port=None, base_path=None,
            error="not a service-mode integration",
        )

    build_section = manifest.get("build") or {}
    stack_name = build_section.get("stack") or build_section.get("type") or "unknown"
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=None,
            error=f"unknown stack '{stack_name}'",
        )

    base_path = f"/apps/{canonical}"
    health_path = launch_section.get("health_check", {}).get(
        "path"
    ) or spec.health_path or "/"

    # ── existing process probe ────────────────────────────────────────
    state = _read_state(canonical)
    if state and _is_alive(state.get("pid")) and _is_healthy(
        state.get("port"), health_path, root=base_path,
        prefix_aware=stack_name in _PREFIX_AWARE_STACKS,
    ):
        strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
        # Re-register on every confirmed-alive probe if state says we missed
        # it last time (Caddy admin was down during the previous launch).
        needs_caddy = not bool(state.get("caddy_registered"))
        caddy_ok = True
        if needs_caddy:
            caddy_ok = _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
            if caddy_ok:
                state["caddy_registered"] = True
                _write_state(canonical, state)
        else:
            # Idempotent refresh; harmless if Caddy already has the route.
            _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
        return LaunchResult(
            slug=slug, action="already_running",
            port=int(state["port"]), base_path=base_path, pid=int(state["pid"]),
        )

    # ── SIF dispatch (preferred when a SIF was built for this slug) ───
    # When a SIF exists, we start (or reuse) an apptainer instance and exec
    # the canonical argv inside it. The host process we supervise is the
    # ``apptainer exec`` Popen — its lifecycle mirrors the in-container
    # server, so the existing pid + cmdline guarding still applies.
    if sif_path is not None and sif_path.exists():
        return _launch_via_sif(
            workspace=workspace,
            slug=slug,
            canonical=canonical,
            spec=spec,
            manifest=manifest,
            stack_name=stack_name,
            base_path=base_path,
            health_path=health_path,
            sif_path=sif_path,
            db=db,
        )

    # ── allocate port ─────────────────────────────────────────────────
    try:
        port = port_allocator.allocate_port(db, app_id=canonical, scope="app")
    except Exception as exc:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"port allocation failed: {exc}",
        )

    # ── compose command ──────────────────────────────────────────────
    # 실패 경로에서 포트를 release 하지 않는다. allocate_port 의 idempotency(0단계)
    # 는 이 앱이 이미 물고 있던 — 살아있는 인스턴스가 지금 bind 중일 수도 있는 —
    # 포트를 돌려줄 수 있다(헬스 프로브가 일시 타임아웃으로 오판해 콜드스타트에
    # 들어온 경우). 여기서 release 하면 그 live 포트가 다른 앱에 재할당돼 Caddy 가
    # 교차 라우팅된다. 포트는 앱에 파킹된 상태로 두는 게 불변식 — 재기동 시 같은
    # 포트를 idempotent 하게 재사용하고, 해제는 프로세스를 실제로 죽이는 stop() 만.
    try:
        argv = _argv_for(workspace, spec, manifest, port=port, base_path=base_path)
    except Exception as exc:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"argv build failed: {exc}",
        )

    log_file = LOG_DIR / f"integration_{canonical}.log"
    # 호스트 프로세스 경로 — bind mount 가 없으므로 영구 데이터 디렉터리의 호스트
    # 절대경로 자체를 $HEAX_DATA_DIR 로 준다(SIF 경로의 /data 와 같은 역할).
    app_data = _app_data_dir(canonical)
    env = os.environ.copy()
    # 매니페스트 앱별 env(launch.env) 먼저, HEAXHub 제어 변수는 아래에서 덮는다.
    env.update(_inherited_env())          # 공용 LLM 설정(있을 때만)
    env.update({
        str(k): str(v)
        for k, v in ((manifest.get("launch") or {}).get("env") or {}).items()
    })
    # ⚠ 검색 정책만은 매니페스트보다 호스트가 이긴다. 앱 매니페스트가 SEARCH_MODE 를
    # 리터럴로 박아 두면(web-research-mcp 가 그렇다) 운영자가 호스트 env 로 아무리 바꿔도
    # 반영되지 않는다. 더 나쁜 조합도 생긴다 — SEARCH_MODE 는 매니페스트에 덮이는데
    # WEB_PROVIDER 는 매니페스트에 없어 그대로 통과하면, 도구는 등록되는데 호출은 전부
    # 차단되는 함정 도구가 만들어진다. "일반 웹 금지"는 앱이 아니라 조직이 정하는 값이므로
    # 여기서 마지막에 덮는다. 호스트에 값이 없으면 아무것도 안 덮으므로 매니페스트가 남는다.
    env.update({k: os.environ[k] for k in _SEARCH_ENV if os.environ.get(k)})
    env.update({
        "PORT": str(port),
        # 앱은 loopback 에만 바인드 — 포트를 외부에 노출하지 말고 리버스프록시(Caddy)
        # 로만 닿게 한다. argv 로 바인드를 제어 못 하는 스택(dash/node/go 등)은 앱이
        # 이 $HOST 를 읽어 바인드해야 한다(컨벤션).
        "HOST": "127.0.0.1",
        "HEAX_DATA_DIR": str(app_data),
        "ROOT_PATH": base_path,
        "BASE_URL_PATH": base_path,
        "NEXT_PUBLIC_BASE_PATH": base_path,
        # Stack-specific sub-path env vars. We set them unconditionally because
        # they're inert when the framework doesn't read them, and saves us
        # branching per stack here.
        "SCRIPT_NAME": base_path,           # Flask / WSGI standard
        "BASE_PATH": base_path,             # nodejs_express / go_service convention
        "HEAX_BASE_PATH": base_path,        # generic HEAXHub-side convention
        "DASH_URL_BASE_PATHNAME": base_path + "/",  # Plotly Dash needs trailing slash
    })
    env.update(_sibling_app_urls(db, canonical))  # HEAX_APP_URL_<SLUG> (HH-09)

    logger.info("launching %s on :%d (root=%s) → %s",
                canonical, port, base_path, argv)
    # Open the per-integration log append+binary so multiple workers can't
    # truncate each other and we tolerate non-UTF-8 binary output from the
    # child (rare but happens with some launchers).
    try:
        log_fh = _open_app_log(log_file)
    except OSError as exc:
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"could not open log {log_file}: {exc}",
        )
    try:
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(workspace),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # behaves like setsid
            close_fds=True,
        )
    except Exception as exc:
        log_fh.close()
        return LaunchResult(
            slug=slug, action="failed", port=None, base_path=base_path,
            error=f"Popen failed: {exc}",
        )

    # ── wait for health ──────────────────────────────────────────────
    healthy = False
    for _ in range(_HEALTH_WAIT_SECONDS):
        time.sleep(1)
        if proc.poll() is not None:
            return LaunchResult(
                slug=slug, action="failed", port=port, base_path=base_path,
                pid=proc.pid,
                error=f"process exited early code={proc.returncode}; tail of {log_file}",
            )
        if _is_healthy(port, health_path, root=base_path,
                      prefix_aware=stack_name in _PREFIX_AWARE_STACKS):
            healthy = True
            break

    if not healthy:
        # process is up but health didn't respond — could be a slow Next.js
        # cold start. Register the route anyway and let the operator decide.
        logger.warning("%s did not pass health within %ds; registering route anyway",
                       canonical, _HEALTH_WAIT_SECONDS)

    # Stacks that handle their own base path (Streamlit, Next.js, Dash, Shiny)
    # need Caddy to pass through the full /apps/<slug> prefix so their internal
    # routers see canonical paths. Everything else strips the prefix.
    strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
    caddy_ok = _safe_register_caddy(canonical, port, base_path, strip_prefix)

    _write_state(canonical, {
        "schema_version": _STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": proc.pid,
        "port": port,
        "base_path": base_path,
        "health_path": health_path,
        "stack": stack_name,
        "argv": argv,
        "caddy_registered": caddy_ok,
        "started_at": time.time(),
    })

    return LaunchResult(
        slug=slug, action="started", port=port, base_path=base_path, pid=proc.pid,
    )


_APP_LOG_ROTATE_BYTES = 64 * 1024 * 1024


def _open_app_log(log_file: Path):
    """앱 로그를 덧붙여 연다 — 새로 띄울 때만 불린다. 이보다 크면 한 벌(.1)만 넘기고 새로 연다(10-17 cae00 리뷰 X4 — 통합 로그
    392MB). 넘기기에 실패해도 기동은 막지 않는다."""
    try:
        if log_file.stat().st_size > _APP_LOG_ROTATE_BYTES:
            os.replace(log_file, log_file.with_name(log_file.name + ".1"))
    except OSError:
        pass
    return log_file.open("ab")


def _sif_fail(slug: str, canonical: str, port: int | None, base_path: str,
              log_file: Path, error: str, *, instance_name: str | None = None) -> LaunchResult:
    """기동 실패를 **앱 로그에도 남기고** 실패 결과를 돌려준다.

    왜 — 기동 전(포트·argv·instance start) 실패는 지금까지 앱 로그 파일을 **아예 만들지 않았다.**
    운영자는 `var/logs/integration_<앱>.log` 를 보러 가는데 파일이 없거나 옛 내용뿐이라, 앱이 왜
    안 뜨는지 알 길이 없었다(worker.log 한 줄은 수천 줄 사이에 묻힌다). 2026-09-20 cae00 의
    `heax_demo_nextjs` 가 그 모양이다 — 라우트가 없어 허브 첫 화면이 200 으로 돌아오는데,
    **이유를 적어 둔 자리가 없다.**

    상태 파일은 일부러 건드리지 않는다 — `app/api/v1/mcp.py:_ever_launched` 가 상태 파일의 **존재**로
    "한 번이라도 뜬 앱" 을 판정한다. 실패에 상태를 쓰면 한 번도 못 뜬 앱이 게이트웨이에 등록돼
    영구 다운 백엔드가 된다(그 주석이 막으려던 바로 그것).

    ``instance_name`` 이 주어지면(= instance start 가 이미 성공한 뒤의 실패) 그 인스턴스를 멈춘다
    (HH-06) — 안 그러면 exec 실패·프로세스 조기종료 뒤 **앱 없는 인스턴스 셸**이 떠 있는 채로
    남는다. instance start 전의 실패(포트 할당 · argv 구성)는 instance_name 을 안 넘겨 아무것도
    멈추지 않는다(멈출 인스턴스가 없다). 존재하지 않는 인스턴스를 멈추는 호출은 안전한 no-op."""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] launch failed: {error}\n")
    except OSError:  # 로그를 못 써도 기동 판정 자체를 막지는 않는다
        pass
    if instance_name:
        try:
            apt_runner.instance_stop(instance_name, check=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("apptainer instance stop failed for %s (%s): %s",
                           canonical, instance_name, exc)
    logger.warning("%s launch failed: %s", canonical, error)
    return LaunchResult(slug=slug, action="failed", port=port, base_path=base_path, error=error)


def _launch_via_sif(
    *,
    workspace: Path,
    slug: str,
    canonical: str,
    spec: StackSpec,
    manifest: dict[str, Any],
    stack_name: str,
    base_path: str,
    health_path: str,
    sif_path: Path,
    db,
) -> LaunchResult:
    """SIF-backed dispatch — start instance, exec the canonical argv inside it.

    Side-effects mirror the host-mode launch:
      * allocate a port via ``port_allocator``,
      * write a state file (schema v2 with ``instance_name`` + ``sif_path``),
      * register the Caddy route.
    """
    instance_name = _instance_name_for(canonical)
    log_file = LOG_DIR / f"integration_{canonical}.log"

    # ── reuse already-running instance + state ────────────────────────
    try:
        running_instances = set(apt_runner.instance_list())
    except Exception:  # pragma: no cover - defensive
        running_instances = set()

    state = _read_state(canonical)
    if (
        state
        and state.get("instance_name") == instance_name
        and instance_name in running_instances
        and _is_alive(state.get("pid"))
        and _is_healthy(state.get("port"), health_path, root=base_path,
                       prefix_aware=stack_name in _PREFIX_AWARE_STACKS)
    ):
        strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
        needs_caddy = not bool(state.get("caddy_registered"))
        if needs_caddy:
            ok = _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
            if ok:
                state["caddy_registered"] = True
                _write_state(canonical, state)
        else:
            _safe_register_caddy(
                canonical, int(state["port"]), base_path, strip_prefix,
            )
        return LaunchResult(
            slug=slug, action="already_running",
            port=int(state["port"]), base_path=base_path, pid=int(state["pid"]),
        )

    # ── allocate port ─────────────────────────────────────────────────
    try:
        port = port_allocator.allocate_port(db, app_id=canonical, scope="app")
    except Exception as exc:
        return _sif_fail(slug, canonical, None, base_path, log_file,
                         f"port allocation failed: {exc}")

    # ── compose container-side argv ───────────────────────────────────
    # 실패 경로에서 포트를 release 하지 않는다(호스트 경로와 동일한 불변식).
    # idempotent allocate 가 살아있는 인스턴스의 포트를 돌려준 상황(헬스 프로브
    # 일시 오판 → 콜드스타트 재진입)에서 release 하면 그 live 포트가 다른 앱에
    # 재할당돼 교차 라우팅된다. 포트는 앱에 파킹 — 해제는 stop() 만 한다.
    try:
        container_argv = _sif_argv_for(spec, manifest, stack_name, port=port, base_path=base_path)
    except Exception as exc:
        return _sif_fail(slug, canonical, None, base_path, log_file,
                         f"argv build failed: {exc}")

    # ── ensure the instance is running ────────────────────────────────
    # SIF rootfs 는 read-only 라 앱이 여기에 못 쓴다. 영구 쓰기 경로를 /data 로
    # bind 하고 $HEAX_DATA_DIR 로 알려준다(예: SQLite/업로드). var/app_data/<id>/
    # 라 재스캔·재빌드·upstream 재fetch 뒤에도 데이터가 남는다.
    app_data = _app_data_dir(canonical)
    binds: list[tuple[str, str]] = [
        (str(workspace), "/workspace"),
        (str(app_data), "/data"),
    ]
    # 사내 CA 번들 — 프록시가 TLS 를 재서명하는 환경에서 컨테이너 기본 CA 로는 검증이 깨진다.
    # 호스트 파일을 컨테이너 고정 경로로 bind 해야 앱이 읽을 수 있다(경로만 넘기면 안 보인다).
    _ca_host = _ca_bundle_host_path()
    if _ca_host:
        binds.append((_ca_host, _CA_IN_CONTAINER))
    # 매니페스트가 선언한 앱별 env(launch.env)를 먼저 깔고 HEAXHub 제어 변수로 덮는다
    # (PORT/HOST/ROOT_PATH/HEAX_DATA_DIR 등은 항상 HEAXHub 가 결정 — 앱이 override
    # 못 함). 앱이 쓰기 경로가 필요하면 $HEAX_DATA_DIR(=/data) 아래를 쓰거나,
    # launch.env 로 앱 고유 변수를 /data 로 가리키면 된다(예: FOO_DATA_DIR: /data).
    env_in_container: dict[str, str] = dict(_inherited_env())   # 공용 LLM 설정(있을 때만)
    env_in_container.update({
        str(k): str(v)
        for k, v in ((manifest.get("launch") or {}).get("env") or {}).items()
    })
    # ⚠ 검색 정책만은 매니페스트보다 호스트가 이긴다. 앱 매니페스트가 SEARCH_MODE 를
    # 리터럴로 박아 두면(web-research-mcp 가 그렇다) 운영자가 호스트 env 로 아무리 바꿔도
    # 반영되지 않는다. 더 나쁜 조합도 생긴다 — SEARCH_MODE 는 매니페스트에 덮이는데
    # WEB_PROVIDER 는 매니페스트에 없어 그대로 통과하면, 도구는 등록되는데 호출은 전부
    # 차단되는 함정 도구가 만들어진다. "일반 웹 금지"는 앱이 아니라 조직이 정하는 값이므로
    # 여기서 마지막에 덮는다. 호스트에 값이 없으면 아무것도 안 덮으므로 매니페스트가 남는다.
    env_in_container.update({k: os.environ[k] for k in _SEARCH_ENV if os.environ.get(k)})
    env_in_container.update({
        "PORT": str(port),
        # loopback 전용 바인드 컨벤션 — argv 로 못 바꾸는 스택은 앱이 $HOST 를 읽어야 함.
        "HOST": "127.0.0.1",
        "HEAX_DATA_DIR": "/data",
        "ROOT_PATH": base_path,
        "BASE_URL_PATH": base_path,
        "NEXT_PUBLIC_BASE_PATH": base_path,
        "SCRIPT_NAME": base_path,
        "BASE_PATH": base_path,
        "HEAX_BASE_PATH": base_path,
        "DASH_URL_BASE_PATHNAME": base_path + "/",
    })
    env_in_container.update(_sibling_app_urls(db, canonical))  # HEAX_APP_URL_<SLUG> (HH-09)
    # CA 를 bind 한 경우에만 각 런타임이 보는 변수를 채운다. bind 없이 변수만 세우면
    # 존재하지 않는 경로를 가리켜 TLS 가 통째로 실패한다 — 안 넣느니만 못하다.
    if _ca_host:
        env_in_container.update({k: _CA_IN_CONTAINER for k in _CA_ENV_TARGETS})
    # SRV-04: cgroup limits from the manifest's resources block (best-effort —
    # only applied when settings.enforce_instance_limits is on).
    _res = manifest.get("resources") if isinstance(manifest.get("resources"), dict) else {}
    _mem_gb = _res.get("memory_gb")
    _cpu = _res.get("cpu")
    _memory = f"{int(float(_mem_gb) * 1024)}m" if _mem_gb else None
    _cpus = str(_cpu) if _cpu else None
    # GPU: 매니페스트가 resources.gpu 를 요청하고 호스트에 실제 NVIDIA 가 있을 때만
    # --nv 를 켠다. GPU 없으면 CPU 폴백(앱 torch 가 CUDA 미탐지로 CPU 로 돈다).
    _nv = bool(_res.get("gpu")) and _host_has_nvidia()

    if instance_name not in running_instances:
        try:
            _cp = apt_runner.instance_start(
                sif=sif_path,
                name=instance_name,
                binds=binds,
                cleanenv=True,
                env=env_in_container,
                memory=_memory,
                cpus=_cpus,
                nv=_nv,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            return _sif_fail(slug, canonical, port, base_path, log_file, str(exc))
        # ⚠ **실패는 예외가 아니라 종료코드로 온다.** `apt_runner.run` 은 `check=True` 를 쓰지 않으므로
        # 여기 있던 `except subprocess.CalledProcessError` 는 **한 번도 실행되지 않는 죽은 코드**였다.
        # 그래서 `instance start` 가 실패해도 그대로 exec 로 내려갔고, 남는 이유는 "process exited
        # early code=1" 이라는 엉뚱한 문장이었다 — 진짜 원인(포트 충돌·SIF 손상·마운트 실패)은 버려졌다.
        # "already exists" 는 실패가 아니다 — 목록 조회가 한 박자 늦었을 뿐 인스턴스는 거기 있다.
        if getattr(_cp, "returncode", 0):
            _err = ((getattr(_cp, "stderr", "") or getattr(_cp, "stdout", "") or "")).strip()
            if "already exists" not in _err:
                return _sif_fail(slug, canonical, port, base_path, log_file,
                                 f"apptainer instance start rc={_cp.returncode}: {_err[-400:]}",
                                 instance_name=instance_name)

    # ── exec the foreground server inside the instance ───────────────
    try:
        log_fh = _open_app_log(log_file)
    except OSError as exc:
        return LaunchResult(
            slug=slug, action="failed", port=port, base_path=base_path,
            error=f"could not open log {log_file}: {exc}",
        )
    try:
        proc = apt_runner.instance_exec(
            instance_name,
            container_argv,
            env=env_in_container,
            cleanenv=True,
            cwd=str(workspace),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except Exception as exc:
        log_fh.close()
        return _sif_fail(slug, canonical, port, base_path, log_file,
                         f"apptainer exec failed: {exc}", instance_name=instance_name)

    # ── wait for health ──────────────────────────────────────────────
    healthy = False
    for _ in range(_HEALTH_WAIT_SECONDS):
        time.sleep(1)
        if proc.poll() is not None:
            res = _sif_fail(slug, canonical, port, base_path, log_file,
                            f"process exited early code={proc.returncode}; tail of {log_file}",
                            instance_name=instance_name)
            res.pid = proc.pid
            return res
        if _is_healthy(port, health_path, root=base_path,
                      prefix_aware=stack_name in _PREFIX_AWARE_STACKS):
            healthy = True
            break
    if not healthy:
        logger.warning("%s (SIF) did not pass health within %ds; registering route anyway",
                       canonical, _HEALTH_WAIT_SECONDS)

    strip_prefix = stack_name not in _PREFIX_AWARE_STACKS
    caddy_ok = _safe_register_caddy(canonical, port, base_path, strip_prefix)

    _write_state(canonical, {
        "schema_version": _STATE_SCHEMA_VERSION,
        "slug": canonical,
        "pid": proc.pid,
        "port": port,
        "base_path": base_path,
        "health_path": health_path,
        "stack": stack_name,
        "argv": [str(sif_path), *container_argv],  # for the PID-reuse guard
        "instance_name": instance_name,
        "sif_path": str(sif_path),
        "caddy_registered": caddy_ok,
        "started_at": time.time(),
    })

    return LaunchResult(
        slug=slug, action="started", port=port, base_path=base_path, pid=proc.pid,
    )


def _instance_name_for(canonical: str) -> str:
    """Build the apptainer instance name from the canonical app id.

    Apptainer instance names must match ``[A-Za-z0-9_-]+`` and stay short
    enough to fit comfortably in PID-file names — we conservatively allow
    underscores/hyphens and rewrite anything else.
    """
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in canonical)
    return f"heax_app_{safe}"


# 호스팅 앱에 물려줄 LLM 설정. 앱은 "env-kits 상속"을 전제로 LLM_BASE_URL / LLM_MODEL 을
# 읽는데(예: WebDesignAgents 무인 심의), 런처가 주입하지 않아 앱이 자체 기본값
# (dev: 127.0.0.1:8000/v1, qwen2.5-7b-dev)으로 폴백했다 — cae00 처럼 그 주소에 아무것도 없는
# 환경에서는 조용히 실패한다. --cleanenv 컨테이너라 명시 전달 외에는 들어갈 방법이 없다.
#
# ⚠ 호스트 쪽 이름을 HEAX_APP_LLM_* 로 분리한 이유: HEAXHub 자신도 LLM_MODEL 을 쓰기 때문에
# 같은 이름을 쓰면 둘이 서로 덮어쓴다(실측: .env 에 LLM_MODEL 이 이미 있었다).
# 호스트 HEAX_APP_LLM_BASE_URL → 컨테이너 LLM_BASE_URL 로 이름을 바꿔 넣는다.
_APP_LLM_ENV = {
    "HEAX_APP_LLM_BASE_URL": "LLM_BASE_URL",
    "HEAX_APP_LLM_MODEL": "LLM_MODEL",
    "HEAX_APP_LLM_API_KEY": "LLM_API_KEY",
    "HEAX_APP_LLM_DISABLE_STREAMING": "LLM_DISABLE_STREAMING",
}


# 사내 프록시 — 운영 박스는 프록시 뒤에 있고 컨테이너는 --cleanenv 로 뜨므로,
# 호스트에 HTTPS_PROXY 를 설정해도 앱 안에는 들어가지 않는다. 그래서 인터넷을 쓰는 앱
# (웹 리서치 등)이 '연결은 되는데 아무것도 못 가져오는' 상태가 된다.
# 대소문자 둘 다 넘긴다 — urllib 은 소문자 http_proxy 도 보고, 라이브러리마다 참조가 다르다.
# NO_PROXY 를 빠뜨리면 사내 목적지까지 프록시로 나가 되레 막힌다.
_PROXY_ENV = (
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "all_proxy",
)
# 외부 검색 정책. 매니페스트(launch.env)가 아니라 여기로 넘기는 이유 —
# "일반 웹 검색 금지" 같은 것은 앱이 아니라 조직이 정하는 값이고, 매니페스트 값은
# 리터럴이라 운영자가 바꾸려면 앱을 다시 등록해야 한다. 호스트 env 로 두면 재빌드
# 없이 켜고 끈다. 값이 없으면 앱 기본값(offline)이 그대로 남는다 — 실수로 열리지 않는다.
_SEARCH_ENV = ("SEARCH_MODE", "WEB_PROVIDER", "BRAVE_API_KEY", "SEARXNG_URL")
# 사내 프록시가 TLS 를 중간에서 재서명하면 컨테이너 기본 CA 로는 검증에 실패한다.
# 인증서 검증을 끄는 것은 답이 아니므로(MITM 무조건 신뢰), 사내 루트 CA 를 넣어 준다.
# 호스트 HEAX_APP_CA_BUNDLE 에 파일 경로를 두면 컨테이너로 bind 하고 아래 변수들을 채운다.
_CA_ENV_TARGETS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
_CA_IN_CONTAINER = "/etc/heax-ca-bundle.crt"


def _inherited_env() -> dict[str, str]:
    """호스트에서 앱 컨테이너로 물려줄 환경변수.

    HEAX_APP_LLM_* 은 앱이 읽는 이름으로 바꿔 넣고, 프록시·검색정책은 이름 그대로 넘긴다.
    """
    out = {dst: os.environ[src] for src, dst in _APP_LLM_ENV.items() if os.environ.get(src)}
    out.update({k: os.environ[k] for k in _PROXY_ENV if os.environ.get(k)})
    out.update({k: os.environ[k] for k in _SEARCH_ENV if os.environ.get(k)})
    return out


def _sibling_app_urls(db, exclude_canonical: str) -> dict[str, str]:
    """다른 앱들의 허브 프록시 주소를 ``HEAX_APP_URL_<SLUG>`` 로 돌려준다(HH-09).

    StepForge 가 MaterialTwin 을 부를 때 기본 주소가 고정 포트(``127.0.0.1:9233``)라
    HEAXHub 가 배정한 실제 포트와 어긋났다 — 포트는 재기동마다 바뀔 수 있어 앱이 미리
    알 방법이 없었다(cae00 운영 수정요청 2026-10-27 HH-09). 포트 대신 안정적인 허브
    프록시(Caddy, ``/apps/<id>``)를 가리키면 포트가 바뀌어도 주소는 그대로다.

    proxy·service·static 모드만 ``/apps/<id>`` 라우트가 실제로 있다 — url·iframe 모드는
    브라우저가 직접 여는 바깥 링크라 호출할 백엔드가 없다(그 집합은
    ``integration_tasks._RECONCILE_MODES`` 와 같다 — 거기서 이 모듈을 import 하므로
    순환참조를 피해 그대로 다시 쓴다). db 가 없으면(단위테스트 등) 빈 dict.
    """
    if db is None:
        return {}
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from app.config import get_settings  # noqa: PLC0415
        from app.db.models.app import App  # noqa: PLC0415
        from app.db.models.app_version import AppVersion  # noqa: PLC0415

        rows = db.execute(
            select(App.id, AppVersion.manifest_snapshot)
            .join(AppVersion, App.current_version_id == AppVersion.id, isouter=True)
        ).all()
    except Exception:  # noqa: BLE001 — sibling-URL 주입은 부가기능, 실패해도 기동은 계속
        logger.warning("sibling app URL lookup failed — skipping HEAX_APP_URL_* injection",
                       exc_info=True)
        return {}

    origin = f"http://127.0.0.1:{get_settings().public_port}"
    out: dict[str, str] = {}
    for app_id, manifest_snapshot in rows:
        if app_id == exclude_canonical:
            continue
        mode = ((manifest_snapshot or {}).get("launch") or {}).get("mode")
        if mode not in ("service", "static", "proxy"):
            continue
        out[f"HEAX_APP_URL_{app_id.upper()}"] = f"{origin}/apps/{app_id}"
    return out


def _ca_bundle_host_path() -> str | None:
    """사내 CA 번들 경로(호스트). 설정돼 있고 실제로 읽히는 파일일 때만 돌려준다."""
    p = (os.environ.get("HEAX_APP_CA_BUNDLE") or "").strip()
    if not p:
        return None
    if not os.path.isfile(p):
        logger.warning("HEAX_APP_CA_BUNDLE 이 가리키는 파일이 없다: %s — CA 주입 생략", p)
        return None
    return p


def _sif_argv_for(
    spec: StackSpec,
    manifest: dict[str, Any],
    stack_name: str,
    *,
    port: int,
    base_path: str,
) -> list[str]:
    """Container-side argv for the foreground server.

    Inside the SIF the runtimes are installed system-wide, so we use plain
    binary names (gunicorn / uvicorn / streamlit / node) and let the
    container's PATH resolve them. The cwd is the bind-mounted workspace.
    """
    _ = spec  # entrypoint single-sourcing deferred — see STK-01 note below.
    # NOTE (STK-01): folding these branches into `/bin/sh -lc <stacks.yaml
    # entrypoint>` was attempted but reverted: a login shell (`-lc`) re-resolves
    # binaries against the host PATH and loses the SIF's per-stack cwd/PATH
    # context (e.g. fastapi_react's runscript cd's to /app/backend and relies on
    # the SIF-internal uvicorn — `-lc` instead found the host ~/.local/bin/uvicorn
    # and 502'd). The hard-coded argv below run via instance_exec keep that
    # context intact. stacks.yaml entrypoints were synced to these values for
    # documentation accuracy; a safe single-sourcing needs argv tokenization +
    # explicit cwd, tracked as future work.
    # 매니페스트가 진입점을 직접 선언했으면 그것을 쓴다. 이걸 무시하면 스택 기본값
    # (fastapi=app.main:app 등)과 다른 레이아웃의 앱은 영영 못 뜬다 — 실측: WebDesignAgents 는
    # 모듈이 wdweb.app:app 이라 하드코딩 argv 로는 기동 불가였고, 매니페스트에 command 를
    # 넣어도 이 함수가 무시해 증상이 그대로였다. $PORT/$ROOT_PATH/$HOST 는 여기서 치환한다.
    _cmd = (manifest.get("launch") or {}).get("command")
    if isinstance(_cmd, str) and _cmd.strip():
        import shlex as _shlex
        _subst = {"PORT": str(port), "ROOT_PATH": base_path, "HOST": "127.0.0.1"}
        _argv = []
        for tok in _shlex.split(_cmd):
            for k, v in _subst.items():
                tok = tok.replace(f"${{{k}}}", v).replace(f"${k}", v)
            _argv.append(tok)
        if _argv:
            return _argv

    if stack_name == "streamlit":
        return [
            "streamlit", "run", "app.py",
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.baseUrlPath", base_path,
            "--server.headless", "true",
        ]
    if stack_name == "fastapi":
        return [
            "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--root-path", base_path,
        ]
    if stack_name == "fastapi_react":
        # Same as fastapi: the SIF runscript cd's into /app/backend before
        # exec, so app.main:app resolves. The Vite-built frontend at
        # /app/frontend/dist is StaticFiles-mounted by main.py at "/".
        return [
            "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--root-path", base_path,
        ]
    if stack_name == "flask":
        callable_ = (
            (manifest.get("launch") or {}).get("entrypoint_override")
            or "app:app"
        )
        return [
            "gunicorn",
            "--bind", f"127.0.0.1:{port}",
            "--workers", "2",
            "--access-logfile", "-",
            str(callable_),
        ]
    if stack_name == "dash_plotly":
        return ["python", "app.py"]
    if stack_name == "shiny_for_python":
        return [
            "shiny", "run",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--root-path", base_path,
            "app.py",
        ]
    if stack_name in ("nextjs", "node_service"):
        return ["node_modules/.bin/next", "start", "--port", str(port), "--hostname", "127.0.0.1"]
    if stack_name == "nodejs_express":
        return ["node", "dist/server.js"]
    if stack_name == "go_service":
        # go_service.def copies the built binary to /app/bin/server inside the SIF.
        # --pwd /app means the relative path is bin/server, but we use an absolute
        # path so it works regardless of pwd flag interaction.
        return ["/app/bin/server"]
    if stack_name == "dotnet_aspnet":
        # STK-03: artefacts land in /app/publish; the .def symlinks the real
        # entry assembly to /app/publish/app.dll. A manifest may still name a
        # specific assembly, which is resolved under publish/ too.
        override = (manifest.get("launch") or {}).get("assembly") or "app.dll"
        return ["dotnet", f"/app/publish/{override}", "--urls", f"http://127.0.0.1:{port}"]
    if stack_name == "java_springboot":
        return ["java", "-jar", "/app/app.jar", f"--server.port={port}"]
    if stack_name == "rust_actix":
        # rust_actix.def copies the built binary to /app/server inside the SIF.
        return ["/app/server"]
    # Generic fallback: honour manifest.launch.command.
    cmd = (manifest.get("launch") or {}).get("command") or "./.portal/run.sh"
    return ["/bin/sh", "-c", str(cmd)]


def _launch_static(
    workspace: Path,
    canonical: str,
    manifest: dict[str, Any],
    *,
    slug: str | None = None,
    source: dict[str, Any] | None = None,
) -> LaunchResult:
    """Wire a static-runtime stack into Caddy via ``file_server``.

    No port is allocated, no process is spawned. We resolve the configured
    ``static_root`` to an absolute filesystem path (validated already by the
    builder) and ask Caddy's admin API to serve it from ``/apps/{id}/*``.

    When the manifest carries a ``source:`` block, the static_root lives under
    the fetched upstream workspace at
    ``var/integration_workspaces/<slug>/upstream[/<subpath>]/`` instead of the
    in-tree integration directory — the manifest-only pivot leaves the latter
    empty of build artefacts.
    """
    # Local import to avoid an import cycle at module load.
    from app.services import managed_workspaces

    base_path = f"/apps/{canonical}"
    effective_slug = slug or workspace.name

    build_section = manifest.get("build") or {}
    stack_name = build_section.get("stack") or build_section.get("type") or "unknown"
    spec: StackSpec | None = load_stacks().get(stack_name)
    if spec is None:
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"unknown stack '{stack_name}'",
        )

    extra = spec.extra or {}
    # Accept both ``static_root`` (canonical) and ``root`` (shorter alias) so
    # we stay in step with the builder's resolution logic.
    root_rel = (
        build_section.get("static_root")
        or build_section.get("root")
        or extra.get("static_root")
        or "public"
    )
    index_file = (
        build_section.get("index_file")
        or extra.get("index_file")
        or "index.html"
    )

    # Source-aware base path:
    #   - source present → static_root lives under the fetched upstream
    #     workspace (var/integration_workspaces/<slug>/upstream[/subpath])
    #   - source absent  → legacy: resolve under the in-tree integration_dir
    if source and slug:
        subpath = (source.get("subpath") or "")
        base_dir = managed_workspaces.upstream_dir(slug, subpath)
    else:
        base_dir = workspace
    root_abs = (base_dir / root_rel).resolve()
    if not root_abs.is_dir():
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=(
                f"static_root '{root_rel}' missing at {root_abs} — "
                f"did fetch/build run successfully?"
            ),
        )

    try:
        res = proxy_manager.register_static_route(
            app_id=canonical,
            root_path=str(root_abs),
            base_path=base_path,
            index_file=index_file,
        )
    except Exception as exc:
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"caddy register failed: {exc}",
        )
    if not getattr(res, "ok", False):
        return LaunchResult(
            slug=effective_slug, action="failed", port=None, base_path=base_path,
            error=f"caddy register failed: {getattr(res, 'reason', 'unknown')}",
        )
    return LaunchResult(
        slug=effective_slug, action="started", port=None, base_path=base_path,
    )


def stop(canonical: str, *, db) -> bool:
    """Stop a running integration. True if a process was killed.

    When the state records an apptainer ``instance_name`` we route the stop
    through ``apt_runner.instance_stop`` — that terminates every process
    inside the container in one shot, so we don't also need to SIGTERM the
    host-side ``apptainer exec`` pid (it will exit on its own when the
    instance goes away).
    """
    # 운영자가 손으로 내렸다 올리면(redeploy-app.sh — stop() 을 부르는 유일한 곳) 곧바로 다시 한다(위 X4).
    _clear_backoff(canonical)
    state = _read_state(canonical)
    killed = False
    instance_name = state.get("instance_name") if state else None
    if state and instance_name:
        try:
            apt_runner.instance_stop(instance_name, check=False)
            killed = True
        except Exception as exc:
            logger.warning("apptainer instance stop failed for %s (%s): %s",
                           canonical, instance_name, exc)
    elif state and _is_alive(state.get("pid")):
        expected_argv = state.get("argv") or []
        if _pid_matches_argv(int(state["pid"]), expected_argv):
            try:
                os.killpg(os.getpgid(int(state["pid"])), signal.SIGTERM)
                killed = True
            except Exception as exc:
                logger.warning("kill failed for %s pid=%s: %s",
                               canonical, state.get("pid"), exc)
        else:
            # PID reuse: the process at this pid is no longer ours. Don't
            # SIGTERM a stranger; just clean up the stale state.
            logger.warning(
                "refusing to kill pid %s for %s — cmdline does not match "
                "recorded argv (likely PID reuse)",
                state.get("pid"), canonical,
            )
    proxy_manager.unregister_app_route(app_id=canonical)
    if state and state.get("port"):
        try:
            port_allocator.release_port(db, port=int(state["port"]))
        except Exception:
            logger.exception("release_port failed for %s", canonical)
    _delete_state(canonical)
    return killed


# ── 서비스가 아니게 된 앱의 남은 서비스(HH-16, 2026-10-31) ───────────────────────────────────────
# 효과 매니페스트의 launch.mode 가 service 에서 proxy · static · url · iframe · job_runner 같은 다른 모드로 바뀌면(추적 매니페스트로든
# 박스 덮어쓰기로든) 그 앱은 더 띄울 것이 없다. 그런데 예전엔 아무 길도 그 서비스를 내리지 않았다 — proxy 갈래가 상태 파일을
# proxy 기록으로 덮어 instance_name 을 잃고, heax_app_<id> 는 포트와 메모리를 쥔 채 남았다(내려가는 길은 stop.sh 가
# heax_app_* 를 통째로 내릴 때뿐이다). 상태 파일이 '허브가 이 id 로 띄웠다' 고 적은 것만 내린다 — 인스턴스라면 이름이
# _instance_name_for(id) 와 같아야 하고, 호스트 프로세스라면 명령줄이 기록과 맞아야 한다. 다른 앱의 인스턴스는 건드리지 않는다.


def _stale_service_record(state: dict | None) -> dict | None:
    """상태 파일이 '허브가 이 id 로 띄운 서비스' 를 적고 있으면 그 기록을, 아니면 None 을 돌려준다."""
    if not isinstance(state, dict) or state.get("mode") == "proxy":
        return None
    if state.get("instance_name") or (state.get("pid") and state.get("argv")):
        return state
    return None


def _retire_stale_service(canonical: str, *, db) -> bool:
    """상태 파일에 남은 서비스를 내리고 포트를 돌려준다. 끝났으면(내렸거나 이미 없었다) True, 못 내렸으면 False — 기록을 남겨 다음 패스가 다시 한다.

    reconcile(45초) · scan(5분) · 기동 때 reconcile 이 같은 앱을 함께 만질 수 있어 앱별 기동 잠금(HH-12) 안에서 한다.
    다른 워커가 쥐고 있으면 이번에는 건너뛴다.
    """
    with _slug_launch_lock(canonical) as acquired:
        if not acquired:
            logger.info("HH-16: %s — 다른 워커가 이 앱을 만지는 중이라 남은 서비스 정리를 다음 패스로 미룬다", canonical)
            return False
        # 잠금 안에서 다시 읽는다 — 기록을 읽은 뒤 다른 워커가 먼저 정리했으면 손대지 않는다(같은 포트를 두 번 돌려주면 그새
        # 그 포트를 받은 다른 앱의 것을 놓게 된다). 그 사이 다른 서비스 기록으로 바뀌었으면 지금 기록된 것을 내린다.
        rec = _stale_service_record(_read_state(canonical))
        if rec is None:
            return True
        if not _stop_stale_service(canonical, rec):
            return False
        if rec.get("port") and db is not None:
            try:
                port_allocator.release_port(db, port=int(rec["port"]))
            except Exception:  # noqa: BLE001
                logger.exception("HH-16: release_port failed for %s", canonical)
    return True


def _stop_stale_service(canonical: str, rec: dict) -> bool:
    """기록된 인스턴스(또는 호스트 프로세스)를 멈춘다. 멈췄거나 이미 없으면 True, 못 멈췄으면 False."""
    name = rec.get("instance_name")
    stopped = False
    if name:
        expected = _instance_name_for(canonical)
        if name != expected:
            logger.warning("HH-16: %s 상태 파일이 적은 인스턴스 %r 는 이 앱의 이름(%r)이 아니다 — 건드리지 않고 기록만 버린다",
                           canonical, name, expected)
            return True
        try:
            cp = apt_runner.instance_stop(name, check=False, capture_output=True, text=True, timeout=120)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HH-16: %s — 남은 서비스 인스턴스 %s 를 내리지 못했다(%s). 다음 reconcile 이 다시 한다",
                           canonical, name, exc)
            return False
        rc = getattr(cp, "returncode", 1)
        out = " ".join(f"{getattr(cp, 'stderr', '') or ''} {getattr(cp, 'stdout', '') or ''}".split())
        if rc == 0:
            stopped, what = True, f"인스턴스 {name} 를 내렸다"
        elif "no instance found" in out:
            what = f"인스턴스 {name} 는 이미 없다 — 상태 기록만 정리한다"
        else:
            logger.warning("HH-16: %s — 남은 서비스 인스턴스 %s 를 내리지 못했다(rc=%s: %s). 다음 reconcile 이 다시 한다",
                           canonical, name, rc, out[-300:])
            return False
    else:
        pid = rec.get("pid")
        if not _is_alive(pid):
            what = f"호스트 프로세스 pid={pid} 는 이미 없다 — 상태 기록만 정리한다"
        elif not _pid_matches_argv(int(pid), rec.get("argv") or []):
            logger.warning("HH-16: %s — pid %s 의 명령줄이 기록과 달라(pid 재사용) 건드리지 않고 기록만 버린다", canonical, pid)
            return True
        else:
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
            except Exception as exc:  # noqa: BLE001
                logger.warning("HH-16: %s — 남은 호스트 프로세스 pid=%s 를 내리지 못했다(%s). 다음 reconcile 이 다시 한다",
                               canonical, pid, exc)
                return False
            stopped, what = True, f"호스트 프로세스 pid={pid} 를 내렸다"
    msg = f"효과 매니페스트의 launch.mode 가 더는 service 가 아니다 — 허브가 띄워 둔 {what}(HH-16)"
    (logger.warning if stopped else logger.info)("%s: %s", canonical, msg)
    try:  # 그 앱의 로그도 여기서 끊긴다 — 왜 끊겼는지 같은 자리에 남긴다
        with (LOG_DIR / f"integration_{canonical}.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass
    return True


def retire_unrouted(canonical: str, *, db) -> bool:
    """라우트를 걸지 않는 모드(url · iframe · job_runner …)로 바뀐 앱의 남은 서비스를 내린다(HH-16, reconcile 이 부른다).

    내렸으면(또는 이미 없어 기록만 정리했으면) 그 앱의 /apps/<id> 라우트와 상태 파일도 지우고 True. 남은 것이 없거나
    못 내렸으면 False — 못 내린 기록은 그대로 두어 다음 패스가 다시 한다. proxy · static 은 _launch_once 가 새 라우트를 건 뒤 내린다.
    reconcile 은 그 모드를 매니페스트가 '명시했을' 때만 부른다 — 모드가 없거나 모르는 값이면 warn_stale_service 로 알리기만 한다.
    """
    if _stale_service_record(_read_state(canonical)) is None or not _retire_stale_service(canonical, db=db):
        return False
    try:
        proxy_manager.unregister_app_route(app_id=canonical)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HH-16: %s 의 옛 라우트를 지우지 못했다: %s", canonical, exc)
    _delete_state(canonical)
    return True


# 같은 경고를 reconcile(45초)마다 되풀이하지 않는다(HH-08 로그 소음) — 열쇠마다 한 시간에 한 번. 프로세스마다 따로다.
_WARN_REPEAT_S = 3600
_WARNED: dict[str, float] = {}


def _warn_hourly(key: str, msg: str, *args: Any) -> None:
    now = time.monotonic()
    last = _WARNED.get(key)
    if last is not None and now - last < _WARN_REPEAT_S:
        return
    _WARNED[key] = now
    logger.warning(msg, *args)


def warn_stale_service(canonical: str, why: str) -> None:
    """허브가 이 id 로 띄운 서비스 기록이 남았는데 내리지 않기로 했으면 그 까닭을 알린다(같은 까닭은 한 시간에 한 번).

    reconcile 이 launch.mode 가 없거나 모르는 값인 앱에 부른다(HH-16 검토) — 오타(``mode: servise``) · launch 블록 삭제 ·
    git 이 파일을 다시 쓰는 순간의 빈 읽기만으로 도는 서비스를 내리고 라우트 · 상태 파일까지 지우던 자리다. 남은 기록이 없으면 말하지 않는다.
    """
    rec = _stale_service_record(_read_state(canonical))
    if rec is None:
        return
    _warn_hourly(f"{canonical}:{why}",
                 "HH-16: %s — %s. 허브가 띄워 둔 서비스(%s)를 내리지 않는다 — 매니페스트를 고치면 다음 reconcile 이 따른다",
                 canonical, why, rec.get("instance_name") or f"pid={rec.get('pid')}")


# ---------------------------------------------------------------------------
# Command composition
# ---------------------------------------------------------------------------


def _argv_for(
    workspace: Path,
    spec: StackSpec,
    manifest: dict[str, Any],
    *,
    port: int,
    base_path: str,
) -> list[str]:
    """Decide the argv. Prefer manifest.launch.command, else stack template."""
    stack_name = (manifest.get("build") or {}).get("stack")
    venv = workspace / ".venv"

    if stack_name == "streamlit":
        bin_ = venv / "bin" / "streamlit"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "run", "app.py",
            "--server.port", str(port),
            "--server.address", "127.0.0.1",
            "--server.baseUrlPath", base_path,
            "--server.headless", "true",
        ]
    if stack_name == "fastapi":
        bin_ = venv / "bin" / "uvicorn"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--root-path", base_path,
        ]
    if stack_name in ("nextjs", "node_service"):
        pnpm = shutil.which("pnpm") or shutil.which("npm")
        if pnpm is None:
            raise FileNotFoundError("pnpm/npm not on PATH for service launch")
        # Use node_modules/.bin/next directly for tighter control.
        next_bin = workspace / "node_modules" / ".bin" / "next"
        if next_bin.exists():
            return [str(next_bin), "start", "--port", str(port), "--hostname", "127.0.0.1"]
        return [pnpm, "start", "--", "--port", str(port), "--hostname", "127.0.0.1"]
    if stack_name == "flask":
        # gunicorn from the venv binds to $PORT; Flask reads SCRIPT_NAME from
        # env (WSGI standard) so we don't need to pass base_path on argv.
        bin_ = venv / "bin" / "gunicorn"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        callable_ = (
            (manifest.get("launch") or {}).get("entrypoint_override")
            or "app:app"
        )
        return [
            str(bin_),
            "--bind", f"127.0.0.1:{port}",
            "--workers", "2",
            "--access-logfile", "-",
            str(callable_),
        ]
    if stack_name == "nodejs_express":
        # Operator builds to dist/server.js (TypeScript) or commits a plain
        # src/server.js. Prefer dist/, fall back to src/ for non-TS projects.
        node_bin = shutil.which("node")
        if node_bin is None:
            raise FileNotFoundError("node not on PATH for nodejs_express launch")
        dist_entry = workspace / "dist" / "server.js"
        src_entry = workspace / "src" / "server.js"
        entry = dist_entry if dist_entry.exists() else src_entry
        if not entry.exists():
            raise FileNotFoundError(
                f"neither {dist_entry} nor {src_entry} found — did the build run?"
            )
        return [node_bin, str(entry)]
    if stack_name == "go_service":
        # `go build` drops the binary at the project root with the module
        # basename. Older convention was `bin/server`. We probe both so the
        # operator's project layout choice doesn't matter.
        candidates = [
            workspace / "bin" / "server",
            workspace / "bin" / workspace.name,
            workspace / workspace.name,
        ]
        bin_ = next((c for c in candidates if c.exists()), None)
        if bin_ is None:
            raise FileNotFoundError(
                f"go binary not found at any of: {[str(c) for c in candidates]} "
                "— has integration_builder run 'go build' successfully? "
                "Check var/logs/build_*.log for build errors."
            )
        return [str(bin_)]
    if stack_name == "dash_plotly":
        # Dash apps expose `server = app.server` (a Flask WSGI callable). We
        # exec `python app.py` directly so the operator's __main__ block reads
        # PORT/DASH_URL_BASE_PATHNAME from env and is in control of binding —
        # avoids requiring gunicorn in the venv for the demo footprint.
        python_bin = venv / "bin" / "python"
        if not python_bin.exists():
            raise FileNotFoundError(
                f"{python_bin} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [str(python_bin), "app.py"]
    if stack_name == "shiny_for_python":
        # The `shiny` CLI is installed into .venv/bin/shiny by `pip install shiny`.
        # --root-path mounts the ASGI app under the canonical sub-path prefix.
        bin_ = venv / "bin" / "shiny"
        if not bin_.exists():
            raise FileNotFoundError(
                f"{bin_} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        return [
            str(bin_), "run",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--root-path", base_path,
            "app.py",
        ]
    if stack_name == "dotnet_aspnet":
        # Operator runs `dotnet publish -c Release -o publish/`. We resolve the
        # main entry assembly: prefer publish/<override>.dll if the manifest
        # names it, else the first non-shared .dll under publish/. ASP.NET
        # Core reads --urls and binds; Caddy strips /apps/<slug> before proxy
        # so the app sees canonical paths and we don't need a sub-path option.
        dotnet_bin = shutil.which("dotnet")
        if dotnet_bin is None:
            raise FileNotFoundError(
                "dotnet not on PATH — operator must install .NET 8 SDK on the host"
            )
        publish_dir = workspace / "publish"
        if not publish_dir.exists():
            raise FileNotFoundError(
                f"{publish_dir} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        override = (manifest.get("launch") or {}).get("assembly")
        dll_path: Path | None = None
        if override:
            cand = publish_dir / override
            if cand.exists():
                dll_path = cand
        if dll_path is None:
            # Pick the newest .dll that isn't an obvious dependency (ref/Microsoft.*).
            # The MSBuild target writes the entry assembly LAST so mtime sort is
            # a reasonable heuristic for the default project layout.
            dlls = sorted(
                publish_dir.glob("*.dll"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for cand in dlls:
                if cand.name.startswith(("Microsoft.", "System.")):
                    continue
                dll_path = cand
                break
        if dll_path is None:
            raise FileNotFoundError(
                f"no entry .dll found under {publish_dir}; set launch.assembly "
                "in the manifest to disambiguate."
            )
        return [
            dotnet_bin, str(dll_path),
            "--urls", f"http://127.0.0.1:{port}",
        ]
    if stack_name == "java_springboot":
        # Spring Boot reads --server.port from the command-line property bridge.
        # We exec the fat-jar produced by `./mvnw package` (target/*.jar). The
        # operator can pin a specific jar via manifest.launch.jar; otherwise we
        # pick the most-recently-built one that isn't a -sources/-javadoc artifact.
        java_bin = shutil.which("java")
        if java_bin is None:
            raise FileNotFoundError(
                "java not on PATH — operator must install JDK 17 on the host"
            )
        target_dir = workspace / "target"
        override = (manifest.get("launch") or {}).get("jar")
        jar_path: Path | None = None
        if override:
            cand = workspace / override
            if cand.exists():
                jar_path = cand
        if jar_path is None and target_dir.exists():
            jars = sorted(
                (
                    p for p in target_dir.glob("*.jar")
                    if not p.name.endswith(("-sources.jar", "-javadoc.jar"))
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            jar_path = jars[0] if jars else None
        if jar_path is None:
            raise FileNotFoundError(
                f"no runnable jar under {target_dir} — has integration_builder "
                "run successfully? Check var/logs/backend.log for build errors."
            )
        return [
            java_bin, "-jar", str(jar_path),
            f"--server.port={port}",
        ]
    if stack_name == "rust_actix":
        # cargo build --release puts the binary at target/release/<crate_name>.
        # Operator may pin the binary name via manifest.launch.binary; default
        # picks the first executable file under target/release/ that isn't a
        # build artefact (.d / .rlib). The actix app reads $PORT from env
        # (heaxhub injects PORT for every service launch).
        release_dir = workspace / "target" / "release"
        if not release_dir.exists():
            raise FileNotFoundError(
                f"{release_dir} not found — has integration_builder run successfully? "
                "Check var/logs/backend.log for build errors."
            )
        override = (manifest.get("launch") or {}).get("binary")
        bin_path: Path | None = None
        if override:
            cand = release_dir / override
            if cand.exists() and os.access(cand, os.X_OK):
                bin_path = cand
        if bin_path is None:
            for cand in sorted(release_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not cand.is_file():
                    continue
                if cand.name.endswith((".d", ".rlib", ".rmeta")):
                    continue
                if not os.access(cand, os.X_OK):
                    continue
                bin_path = cand
                break
        if bin_path is None:
            raise FileNotFoundError(
                f"no release binary under {release_dir}; set launch.binary in the "
                "manifest to disambiguate."
            )
        return [str(bin_path)]

    # Generic fallback: run manifest.launch.command via /bin/sh
    cmd = (manifest.get("launch") or {}).get("command") or "./.portal/run.sh"
    return ["/bin/sh", "-c", str(cmd)]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _state_path(canonical: str) -> Path:
    return STATE_DIR / f"{canonical}.json"


def _read_state(canonical: str) -> dict | None:
    """Read the state file, ignoring records from a different schema version.

    Older state (no schema_version key, or a different number) is treated as
    nonexistent so the launcher takes the cold-start path instead of crashing
    on a missing field.
    """
    p = _state_path(canonical)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if version is None:
        # Pre-versioned state: upgrade in-memory but trust enough fields to
        # re-register Caddy. If anything is missing the launcher will cold-start.
        data["schema_version"] = _STATE_SCHEMA_VERSION
        return data
    if version != _STATE_SCHEMA_VERSION:
        logger.info(
            "ignoring %s state file with schema_version=%r (current=%d)",
            canonical, version, _STATE_SCHEMA_VERSION,
        )
        return None
    return data


def _write_state(canonical: str, state: dict) -> None:
    """Atomic write with fsync — survives power-loss between write and rename."""
    state.setdefault("schema_version", _STATE_SCHEMA_VERSION)
    target = _state_path(canonical)
    tmp = target.with_suffix(".json.tmp")
    payload = json.dumps(state, indent=2).encode("utf-8")
    # fsync the tmp file before rename so the rename can't be reordered ahead
    # of the bytes hitting the disk platter.
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, target)
    try:
        dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _delete_state(canonical: str) -> None:
    try:
        _state_path(canonical).unlink()
    except FileNotFoundError:
        pass


def _is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _pid_matches_argv(pid: int, expected_argv: list[str]) -> bool:
    """True when /proc/<pid>/cmdline starts with the expected argv prefix.

    Guards against PID-reuse: an unrelated process inherited our recorded pid
    after the integration died and we never noticed. We compare the first
    argv token (the executable) plus the second token if present, which is
    enough to disambiguate ``streamlit run`` from ``ssh root@host`` without
    being overly strict about minor argv differences.
    """
    if not expected_argv:
        # No recorded argv (old state) — assume ok; the worst case is we kill
        # a foreign process, which is the bug we're trying to avoid, but we
        # can't do better without recorded data.
        return True
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            raw = f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return False
    actual = [t.decode("utf-8", errors="replace") for t in raw.split(b"\x00") if t]
    if not actual:
        return False
    # Match on the executable basename so /usr/bin/python3 vs ./.venv/bin/python3
    # don't false-negative. Then require the second token (sub-command) to match
    # too when present.
    exp0 = os.path.basename(expected_argv[0])
    act0 = os.path.basename(actual[0])
    if exp0 != act0:
        return False
    if len(expected_argv) >= 2 and len(actual) >= 2:
        if expected_argv[1] != actual[1]:
            return False
    return True


def _is_healthy(port: int | None, health_path: str, *, root: str, prefix_aware: bool = False) -> bool:
    """Probe several candidate URLs; anything < 500 (including 3xx redirects
    Next.js issues for trailing-slash normalization) counts as healthy.

    ``prefix_aware`` 는 이 앱의 스택이 서브패스를 스스로 처리하는지(``_PREFIX_AWARE_STACKS``,
    Next.js·Streamlit·Dash 등) 를 알려준다. 그 앱들만 ``root+health_path`` 가 맞는 URL이고,
    그 외(대부분) 앱은 Caddy 가 프리픽스를 벗겨 전달하므로 그 URL은 **구조적으로 항상 404**다
    (upstream 을 직접 부르면 200). 그걸 먼저 시도하면 헬스체크마다(45초 reconcile·5분 scan)
    보장된 404 가 로그에 쌓인다(HH-07) — 판정(아래 어느 URL이든 <500 이면 healthy)은 그대로
    두고, 순서만 그 앱 종류에 맞는 URL을 앞에 둔다."""
    if not port:
        return False
    prefixed = f"http://127.0.0.1:{port}{root}{health_path}"
    bare = f"http://127.0.0.1:{port}{health_path}"
    urls = (
        [prefixed, bare] if prefix_aware else [bare, prefixed]
    ) + [
        f"http://127.0.0.1:{port}{root}",  # plain root (Next.js redirect)
        f"http://127.0.0.1:{port}{root.rstrip('/')}",  # bare slug, no trailing slash
    ]
    for url in urls:
        try:
            r = httpx.get(url, timeout=_HEALTH_TIMEOUT, follow_redirects=False)
            if r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def _safe_register_caddy(
    app_id: str, port: int, base_path: str, strip_prefix: bool,
) -> bool:
    """Register a Caddy route, returning False on transient admin failures.

    The caller can persist this to know it must retry on next scan instead
    of falsely believing the route is wired up.
    """
    try:
        proxy_manager.register_app_route(
            app_id=app_id, port=port, base_path=base_path,
            strip_prefix=strip_prefix,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Caddy admin unreachable while registering %s; will retry: %s",
            app_id, exc,
        )
        return False


def read_manifest(workspace: Path) -> dict[str, Any] | None:
    """Convenience used by the scanner to feed launch() — 박스 덮어쓰기(manifest.local.yaml)를 얹은 효과 매니페스트(HH-15)."""
    manifest = workspace / ".portal" / "manifest.yaml"
    if not manifest.exists():
        return None
    try:
        return integration_manifest.load_file(manifest)
    except Exception as exc:
        logger.error("manifest load failed for %s: %s", workspace, exc)
        return None
