# 박스 전용 매니페스트 덮어쓰기(manifest.local.yaml, HH-15) — 병합 규칙 · 깨진 파일 · id 지키기 · 스캐너 · reconcile · 상태 화면이 그것을 보는지
#
# 같은 매니페스트 넷을 당기는 박스가 cae00 말고도 있다(dev 는 step_forge 를 서비스로 띄우고 kooremapper 셋을 :8700 · :8701 로 넘긴다).
# 추적 파일만 읽으면 박스에서 고친 값은 다음 당기기에 되돌아간다. 로컬 파일이 없을 때는 예전과 한 글자도 달라지면 안 된다.
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services import integration_launcher as L
from app.services import integration_manifest as M

REPO = Path(__file__).resolve().parents[3]

_BASE = {
    "schema_version": 2,
    "id": "demo_local",
    "name": "Demo Local",
    "version": "0.1.0",
    "status": "beta",
    "app_type": "web_app",
    "execution_target": "external_url",
    "tags": ["a", "b"],
    "build": {"stack": "external_proxy"},
    "launch": {"mode": "proxy", "upstream": "http://127.0.0.1:16110", "strip_prefix": True, "portal_auth": True},
    "permissions": {"visibility": "company"},
    "mcp": {"expose": True, "path": "/mcp", "transport": "streamable_http"},
}


@pytest.fixture(autouse=True)
def _fresh_notes():
    """로그 한 번만 남기기 기록은 프로세스 전역이다 — 시험마다 비운다."""
    M._NOTED.clear()
    yield
    M._NOTED.clear()


def _write(dirpath: Path, base: dict | str, local: dict | str | None = None) -> Path:
    portal = dirpath / ".portal"
    portal.mkdir(parents=True, exist_ok=True)
    (portal / "manifest.yaml").write_text(
        base if isinstance(base, str) else yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    lp = portal / "manifest.local.yaml"
    if local is None:
        if lp.exists():
            lp.unlink()
    else:
        lp.write_text(local if isinstance(local, str) else yaml.safe_dump(local, allow_unicode=True), encoding="utf-8")
    return portal / "manifest.yaml"


# ─── 로컬 파일이 없으면 예전과 같다 ─────────────────────────────────────────────


def test_로컬_파일이_없으면_safe_load_결과_그대로다(tmp_path):
    mp = _write(tmp_path / "app", _BASE)
    got, info = M.load_file_with_override(mp)
    assert info is None
    assert got == yaml.safe_load(mp.read_text(encoding="utf-8"))
    assert json.dumps(got, sort_keys=True) == json.dumps(yaml.safe_load(mp.read_text(encoding="utf-8")), sort_keys=True)


def test_리포의_모든_추적_매니페스트가_예전과_바이트까지_같다():
    """실제 integrations/*/.portal/manifest.yaml 을 읽기만 한다 — 로컬 파일이 없는 판에서 로더 결과가 safe_load 와 같아야 한다."""
    paths = sorted((REPO / "integrations").glob("*/.portal/manifest.yaml"))
    assert paths, "integrations/ 에 매니페스트가 하나도 없다"
    for mp in paths:
        if (mp.parent / M.LOCAL_FILE).exists():
            continue  # 이 박스가 실제로 덮어쓴 앱은 같을 수 없다
        old = yaml.safe_load(mp.read_text(encoding="utf-8"))
        new = M.load_file(mp)
        assert json.dumps(new, sort_keys=True, ensure_ascii=False, default=str) == \
            json.dumps(old, sort_keys=True, ensure_ascii=False, default=str), mp


def test_추적_파일의_YAML_오류는_예전처럼_그대로_올린다(tmp_path):
    mp = _write(tmp_path / "app", "id: [broken\n")
    with pytest.raises(yaml.YAMLError):
        M.load_file(mp)
    (mp.parent / "manifest.yaml").unlink()
    with pytest.raises(FileNotFoundError):
        M.load_file(mp)


# ─── 병합 규칙(RFC 7386) ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "patch", "want"),
    [  # RFC 7386 부록 A 의 시험 사례 그대로
        ({"a": "b"}, {"a": "c"}, {"a": "c"}),
        ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
        ({"a": "b"}, {"a": None}, {}),
        ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
        ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
        ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
        ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
        ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
        (["a", "b"], ["c", "d"], ["c", "d"]),
        ({"a": "b"}, ["c"], ["c"]),
        ({"a": "foo"}, None, None),
        ({"a": "foo"}, "bar", "bar"),
        ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
        ([1, 2], {"a": "b", "c": None}, {"a": "b"}),
        ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
    ],
)
def test_merge_patch_는_RFC_7386_사례를_따른다(target, patch, want):
    before_t, before_p = json.dumps(target, sort_keys=True), json.dumps(patch, sort_keys=True)
    assert M.merge_patch(target, patch) == want
    assert json.dumps(target, sort_keys=True) == before_t and json.dumps(patch, sort_keys=True) == before_p, "입력을 바꿨다"


def test_중첩은_합치고_null_은_지우고_목록은_통째로_바꾼다(tmp_path):
    local = {
        "launch": {"upstream": None, "strip_prefix": None, "mode": "service", "health_check": {"type": "http", "path": "/health"}},
        "build": {"stack": "fastapi", "type": "python_venv"},
        "tags": ["dev"],
        "source": {"type": "git", "url": "https://example.com/StepForge.git", "ref": "main"},
        "mcp": None,
    }
    got, info = M.load_file_with_override(_write(tmp_path / "app", _BASE, local))
    assert got["launch"] == {"mode": "service", "portal_auth": True, "health_check": {"type": "http", "path": "/health"}}
    assert got["build"] == {"stack": "fastapi", "type": "python_venv"}
    assert got["tags"] == ["dev"], "목록은 이어 붙이지 않고 통째로 바꾼다"
    assert got["source"]["url"].endswith("StepForge.git")
    assert "mcp" not in got, "null 은 그 키를 지운다"
    assert got["permissions"] == _BASE["permissions"] and got["name"] == _BASE["name"], "패치에 없는 키는 그대로다"
    assert info is not None and info.applied and info.error is None
    assert info.changed_keys == ["build", "launch", "mcp", "source", "tags"]


def test_바뀐_키는_실제로_달라진_것만_센다(tmp_path):
    local = {"status": "beta", "launch": {"portal_auth": True}, "version": "9.9.9"}  # 앞의 둘은 추적 값과 같다
    _, info = M.load_file_with_override(_write(tmp_path / "app", _BASE, local))
    assert info.changed_keys == ["version"]


# ─── 깨진 로컬 파일은 무시하고 추적 매니페스트로 돈다 ──────────────────────────────


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ("launch: {mode: service\n", "YAML 로 읽히지 않는다"),
        ("- a\n- b\n", "매핑이 아니다(list)"),
        ("just text\n", "매핑이 아니다(str)"),
        ("", "매핑이 아니다(빈 파일)"),
        (b"\xff\xfe\x00bad", "YAML 로 읽히지 않는다"),
    ],
)
def test_깨진_로컬_파일은_무시하고_크게_알린다(tmp_path, caplog, raw, why):
    mp = _write(tmp_path / "app", _BASE)
    lp = mp.with_name(M.LOCAL_FILE)
    lp.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
    with caplog.at_level(logging.WARNING, logger=M.logger.name):
        got, info = M.load_file_with_override(mp)
    assert got == yaml.safe_load(mp.read_text(encoding="utf-8")), "추적 매니페스트로 돌아야 한다"
    assert info is not None and not info.applied and why in (info.error or "")
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and str(lp) in warned[0].getMessage() and "무시" in warned[0].getMessage()


def test_디렉터리라_못_읽어도_추적_매니페스트로_돈다(tmp_path, caplog):
    mp = _write(tmp_path / "app", _BASE)
    mp.with_name(M.LOCAL_FILE).mkdir()
    with caplog.at_level(logging.WARNING, logger=M.logger.name):
        got, info = M.load_file_with_override(mp)
    assert got == _BASE and not info.applied and "읽지 못했다" in info.error
    assert any("무시" in r.getMessage() for r in caplog.records)


def test_같은_경고는_되풀이하지_않고_내용이_바뀌면_다시_말한다(tmp_path, caplog):
    mp = _write(tmp_path / "app", _BASE, "launch: [\n")
    with caplog.at_level(logging.INFO, logger=M.logger.name):
        for _ in range(5):                    # reconcile 45초 × 5
            M.load_file(mp)
        assert sum("무시" in r.getMessage() for r in caplog.records) == 1
        _write(tmp_path / "app", _BASE, {"launch": {"upstream": "http://127.0.0.1:8700"}})   # 고쳤다
        M.load_file(mp)
        M.load_file(mp)
        assert sum("얹었다" in r.getMessage() for r in caplog.records) == 1
        _write(tmp_path / "app", _BASE, None)                                                # 지웠다
        assert M.load_file(mp) == _BASE
        M.load_file(mp)
        assert sum("없어졌다" in r.getMessage() for r in caplog.records) == 1


# ─── 로컬 파일은 앱 id 를 못 바꾼다 ────────────────────────────────────────────


@pytest.mark.parametrize("new_id", ["other_app", None, ""])
def test_id_바꾸기는_버리고_나머지는_얹는다(tmp_path, caplog, new_id):
    local = {"id": new_id, "launch": {"upstream": "http://127.0.0.1:8700"}}
    with caplog.at_level(logging.WARNING, logger=M.logger.name):
        got, info = M.load_file_with_override(_write(tmp_path / "app", _BASE, local))
    assert got["id"] == "demo_local", "id 는 추적 매니페스트 그대로여야 한다"
    assert got["launch"]["upstream"] == "http://127.0.0.1:8700", "id 말고 나머지는 얹는다"
    assert info.rejected_keys == ["id"] and "id" not in info.changed_keys
    assert any("id" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


def test_같은_id_를_적은_것은_바꾸기가_아니다(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=M.logger.name):
        _, info = M.load_file_with_override(_write(tmp_path / "app", _BASE, {"id": "demo_local", "status": "stable"}))
    assert info.rejected_keys == [] and info.changed_keys == ["status"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ─── 무시 목록 — 커밋되지 않고 git stash -u 도 가져가지 않는다 ─────────────────────


def test_gitignore_가_로컬_파일만_무시한다():
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    probe = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("git 작업 트리가 아니다")
    ign = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "--no-index",
                          "integrations/step_forge/.portal/manifest.local.yaml"])
    assert ign.returncode == 0, "manifest.local.yaml 이 무시 목록에 없다"
    tracked = subprocess.run(["git", "-C", str(REPO), "check-ignore", "-q", "--no-index",
                              "integrations/step_forge/.portal/manifest.yaml"])
    assert tracked.returncode == 1, "추적 매니페스트까지 무시되면 안 된다"


def test_git_stash_u_가_로컬_파일을_가져가지_않는다(tmp_path):
    """deploy-all 의 git_update 는 reset --hard 앞에 git stash push -u 를 한다 — 무시된 파일은 -u 가 건드리지 않는다."""
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    repo = tmp_path / "r"
    (repo / "integrations" / "step_forge" / ".portal").mkdir(parents=True)
    shutil.copy(REPO / ".gitignore", repo / ".gitignore")
    (repo / "integrations" / "step_forge" / ".portal" / "manifest.yaml").write_text("id: step_forge\n", encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        assert subprocess.run([*git, *cmd], capture_output=True).returncode == 0, cmd
    local = repo / "integrations" / "step_forge" / ".portal" / "manifest.local.yaml"
    local.write_text("launch: {mode: service}\n", encoding="utf-8")
    (repo / "stray.txt").write_text("x", encoding="utf-8")          # 추적 안 되는 다른 파일 — 이것은 쓸려 가야 정상이다
    assert subprocess.run([*git, "stash", "push", "-u", "-q", "-m", "auto"], capture_output=True).returncode == 0
    assert not (repo / "stray.txt").exists(), "stash -u 가 돌지 않았다(시험 전제가 틀렸다)"
    assert local.read_text(encoding="utf-8") == "launch: {mode: service}\n", "stash -u 가 박스 덮어쓰기를 가져갔다"


# ─── 상태 화면 — 덮어쓴 앱과 바뀐 최상위 키 ─────────────────────────────────────


def test_상태_화면이_덮어쓴_앱만_표시한다(tmp_path, monkeypatch):
    from app.api.v1 import admin
    from app.services import integrations_scanner

    _write(tmp_path / "plain_app", {**_BASE, "id": "plain_app"})
    _write(tmp_path / "step_forge", {**_BASE, "id": "step_forge"},
           {"execution_target": "linux_runner", "launch": {"mode": "service", "upstream": None}})
    _write(tmp_path / "broken_app", {**_BASE, "id": "broken_app"}, "launch: [\n")
    monkeypatch.setattr(integrations_scanner, "INTEGRATIONS_ROOT", tmp_path)

    out = admin.list_builtin_integrations(_admin=None)
    rows = {r["slug"]: r for r in out["items"]}
    assert out["exists"] and set(rows) == {"plain_app", "step_forge", "broken_app"}
    assert "local_override" not in rows["plain_app"], "로컬 파일 없는 앱의 행은 예전과 같아야 한다"
    lo = rows["step_forge"]["local_override"]
    assert lo == {"file": "integrations/step_forge/.portal/manifest.local.yaml", "applied": True,
                  "changed_keys": ["execution_target", "launch"], "rejected_keys": [], "error": None}
    assert rows["step_forge"]["launch_mode"] == "service", "표의 값도 효과 매니페스트다"
    bad = rows["broken_app"]["local_override"]
    assert bad["applied"] is False and "YAML" in bad["error"]
    assert rows["broken_app"]["launch_mode"] == "proxy"


# ─── reconcile 이 로컬 파일을 다음 패스에 따른다 ─────────────────────────────────


class _Proxy:
    """Caddy 관리 API 대역 — 무엇을 어디로 걸었는지만 적는다."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def register_external_proxy_route(self, *, app_id, upstream_url, base_path, strip_prefix=True, portal_auth=False):
        self.calls.append(("proxy", app_id, upstream_url))
        return SimpleNamespace(ok=True)

    def register_app_route(self, *, app_id, port, base_path, strip_prefix=True):
        self.calls.append(("app", app_id, port))
        return SimpleNamespace(ok=True)

    def unregister_app_route(self, *, app_id):
        self.calls.append(("unregister", app_id))
        return SimpleNamespace(ok=True)


@pytest.fixture()
def reconcile_env(tmp_path, monkeypatch):
    from app.workers import integration_tasks

    root = tmp_path / "integrations"
    root.mkdir()
    proxy = _Proxy()
    monkeypatch.setattr(integration_tasks, "_INTEGRATIONS_ROOT", root)
    monkeypatch.setattr(integration_tasks, "_SIF_DIR", tmp_path / "sifs")
    monkeypatch.setattr(integration_tasks, "SessionLocal", lambda: _NullSession())
    monkeypatch.setattr(L, "proxy_manager", proxy)
    return SimpleNamespace(root=root, proxy=proxy, run=integration_tasks.reconcile_integrations)


class _NullSession:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def test_reconcile_은_로컬_파일을_더하고_고치고_지운_것을_다음_패스에_따른다(reconcile_env):
    base = {**_BASE, "id": "kooremapper"}
    d = reconcile_env.root / "kooremapper"
    _write(d, base)
    reconcile_env.run()
    _write(d, base, {"launch": {"upstream": "http://127.0.0.1:8700"}})        # 더했다
    reconcile_env.run()
    _write(d, base, {"launch": {"upstream": "http://127.0.0.1:8701"}})        # 고쳤다
    reconcile_env.run()
    _write(d, base, None)                                                      # 지웠다
    reconcile_env.run()
    ups = [c[2] for c in reconcile_env.proxy.calls if c[0] == "proxy"]
    assert ups == ["http://127.0.0.1:16110", "http://127.0.0.1:8700", "http://127.0.0.1:8701", "http://127.0.0.1:16110"]


def test_깨진_로컬_파일이면_reconcile_은_추적_값으로_돈다(reconcile_env):
    base = {**_BASE, "id": "kooremapper_mcp"}
    _write(reconcile_env.root / "kooremapper-mcp", base, "launch: {upstream: http://127.0.0.1:8701\n")
    reconcile_env.run()
    assert reconcile_env.proxy.calls == [("proxy", "kooremapper_mcp", "http://127.0.0.1:16110")]


def test_되풀이_실패_늦추기도_로컬_파일이_바뀌면_곧바로_다시_한다(reconcile_env, monkeypatch):
    """기동 지문(launch-backoff)이 효과 매니페스트를 본다 — 로컬 파일을 고치면 기다리지 않고 다시 띄운다."""
    calls: list[dict] = []

    def _fail(workspace, *, manifest, db, slug=None, source=None, sif_path=None):
        calls.append(manifest)
        return L.LaunchResult(slug=slug or workspace.name, action="failed", port=None, base_path="/apps/x", error="boom")

    monkeypatch.setattr(L, "_launch_once", _fail)
    base = {**_BASE, "id": "svc_app", "build": {"stack": "fastapi"},
            "launch": {"mode": "service", "health_check": {"path": "/health"}}}
    d = reconcile_env.root / "svc-app"
    _write(d, base)
    first = reconcile_env.run()
    second = reconcile_env.run()
    assert first["by_action"] == {"failed": 1} and second["by_action"] == {"backoff": 1} and len(calls) == 1
    _write(d, base, {"launch": {"env": {"STEPFORGE_DATA_DIR": "/data"}}})
    third = reconcile_env.run()
    assert third["by_action"] == {"failed": 1} and len(calls) == 2, "로컬 파일을 고쳤는데 늦추기에 걸렸다"
    assert calls[-1]["launch"]["env"] == {"STEPFORGE_DATA_DIR": "/data"}


# ─── 스캐너 — 스냅숏 · 빌드 · 기동이 효과 매니페스트를 받는다(DB) ─────────────────────


@pytest.fixture()
def scan_db() -> Iterator:
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from app.db.session import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        pytest.skip("database unreachable; skipping DB-backed scanner test")
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def scan_admin(scan_db):
    from app.config import get_settings
    from app.db.models.user import AuthSource, User, UserRole, UserStatus

    email = f"int-local-{uuid.uuid4().hex[:8]}@example.com"
    user = User(email=email, display_name="Local Override Test", organization="Test", password_hash="x",
                auth_source=AuthSource.LOCAL, status=UserStatus.ACTIVE, role=UserRole.ADMIN, email_verified=True)
    scan_db.add(user)
    scan_db.commit()
    settings = get_settings()
    original = settings.seed_admin_email
    settings.seed_admin_email = email
    try:
        yield user
    finally:
        settings.seed_admin_email = original


def test_스캐너의_스냅숏과_빌드_기동이_로컬_파일을_따른다(tmp_path, scan_db, scan_admin, monkeypatch):
    from app.db.models.app import App
    from app.db.models.app_version import AppVersion
    from app.services import integration_fetcher, integration_sif_builder, integrations_scanner

    built: list[dict] = []
    launched: list[dict] = []

    class _FR:
        action = "updated"
        commit = "a" * 40
        error = None

    class _SR:
        action = "skipped"
        sif = Path("/tmp/fake.sif")
        hash = "h"
        error = None
        log_path = None
        commit = "a" * 40

    def _launch(*a, manifest, **k):
        launched.append(manifest)
        return SimpleNamespace(action="already_running", port=None, base_path="/apps/x", pid=1, error=None)

    monkeypatch.setattr(integration_fetcher, "fetch_for_integration", lambda slug, src: _FR())
    monkeypatch.setattr(integration_sif_builder, "build_sif", lambda slug, m, fr: (built.append(m), _SR())[1])
    monkeypatch.setattr(L, "launch", _launch)

    app_id = f"local_scan_{uuid.uuid4().hex[:8]}"
    base = {**_BASE, "id": app_id, "execution_target": "linux_runner", "build": {"stack": "fastapi"},
            "launch": {"mode": "service", "health_check": {"path": "/health"}},
            "source": {"type": "git", "url": "file:///tmp/repo.git", "ref": "main"}}
    d = tmp_path / "local-scan"
    local = {"launch": {"env": {"STEPFORGE_DATA_DIR": "/data"}}, "resources": {"memory_gb": 4}}

    def _snapshot() -> dict:
        scan_db.expire_all()
        app = scan_db.get(App, app_id)
        return scan_db.get(AppVersion, app.current_version_id).manifest_snapshot

    _write(d, base)
    integrations_scanner.scan_integrations(scan_db, root=tmp_path)
    assert _snapshot() == base
    _write(d, base, local)
    integrations_scanner.scan_integrations(scan_db, root=tmp_path)
    want = M.merge_patch(base, local)
    assert _snapshot() == want, "스냅숏(MCP 레지스트리 · 형제 주소 · API 가 읽는 것)이 로컬 파일을 안 봤다"
    assert built[-1] == want and launched[-1] == want, "빌드 해시 · 기동 지문이 로컬 파일을 안 봤다"
    _write(d, base, None)
    integrations_scanner.scan_integrations(scan_db, root=tmp_path)
    assert _snapshot() == base and built[-1] == base and launched[-1] == base, "로컬 파일을 지운 것이 반영되지 않았다"
