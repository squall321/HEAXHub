# 통합 앱 매니페스트(.portal/manifest.yaml) 위에 박스 전용 덮어쓰기(.portal/manifest.local.yaml)를 RFC 7386 병합 패치로 얹어 읽는 공용 로더
"""통합 앱 매니페스트를 읽는 공용 길 — 추적 파일 위에 박스 전용 덮어쓰기를 얹는다(HH-15, 2026-10-31).

``integrations/<폴더>/.portal/manifest.yaml`` 은 git 이 정본이라 박스에서 고치면 다음 당기기(update-all · deploy-all 의
``git reset --hard``)가 되돌린다. 같은 앱을 박스마다 다르게 돌려야 할 때(운영 박스는 다른 노드로 넘기고 dev 는 여기서
서비스로 띄우는 것 같은)는 같은 폴더의 ``manifest.local.yaml`` 에 그 차이만 적는다. 그 이름은 gitignore 라 커밋되지 않고
``git stash -u`` 도 가져가지 않는다.

병합 규칙은 RFC 7386(JSON Merge Patch)이다.

* 매핑은 키마다 재귀로 합친다.
* 값이 null 이면 그 키를 지운다.
* 목록 · 글자 · 숫자는 통째로 바꾼다 — 목록끼리 이어 붙이지 않는다.

지키는 것.

* 로컬 파일이 없으면 ``yaml.safe_load`` 가 돌려준 것을 그대로 돌려준다 — 예전과 한 글자도 다르지 않다.
* 추적 파일을 읽다 난 예외(파일 없음 · YAML 오류)는 그대로 올린다 — 부르는 쪽의 처리가 예전 그대로 돈다.
* 로컬 파일이 있는데 얹지 못하면(YAML 오류 · 매핑이 아님 · 빈 파일 · 읽히지 않음 · 대상이 없는 심링크 · 얹은 결과의 모양이 틀림)
  그 앱을 **보류한다** — ``load_file`` 이 :class:`LocalOverrideError` 를 올리고, 도는 쪽(스캐너 · reconcile · redeploy-app.sh)은
  그 앱을 이번 패스에서 건너뛰어 라우트 · 인스턴스 · 스냅숏을 마지막 상태 그대로 둔다(WARNING). 추적 값으로 돌지 않는 것이 요점이다 —
  덮어쓰기는 추적 값이 이 박스에 맞지 않아서 둔 것이라, 오타 하나로 추적 값을 따르면 그 앱이 끊기고 HH-16 이 도는 서비스를 내린다.
* 로컬 파일은 앱 id 를 못 바꾼다 — id 는 /apps/<id> · 상태 파일 · 인스턴스 이름(heax_app_<id>)의 원천이다. 그 키만 버린다(WARNING).
* 같은 내용을 되풀이해 알리지 않는다 — reconcile 은 45초마다 돈다. 경고는 내용이 그대로면 한 시간에 한 번만 다시 남긴다.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.logger import get_logger

logger = get_logger(__name__)

MANIFEST_FILE = "manifest.yaml"
LOCAL_FILE = "manifest.local.yaml"

# 같은 경고를 다시 남기기까지의 간격(초) — HH-08 빌드실패 메일 쿨다운과 같은 한 시간이다.
_WARN_REPEAT_S = 3600
# 로컬 파일 경로 → (마지막으로 알린 상태, 그때 시각). 프로세스마다 따로라 uvicorn · celery 워커가 각자 한 번씩 남긴다.
_NOTED: dict[str, tuple[str, float]] = {}
_MISSING = object()


@dataclass(slots=True)
class LocalOverride:
    """``manifest.local.yaml`` 을 어떻게 다뤘는지 — 상태 화면(``GET /admin/integrations/builtin``)이 보여 준다."""

    path: Path
    applied: bool
    changed_keys: list[str] = field(default_factory=list)   # 효과 매니페스트에서 실제로 달라진 최상위 키
    rejected_keys: list[str] = field(default_factory=list)  # 받아들이지 않은 키(지금은 id 하나)
    error: str | None = None                                # 얹지 못한 까닭
    held: bool = False                                      # 로컬 파일이 있는데 얹지 못해 그 앱을 보류한다(추적 값으로 돌지 않는다)


class LocalOverrideError(Exception):
    """``manifest.local.yaml`` 이 있는데 얹지 못했다 — 부르는 쪽은 추적 값으로 돌지 말고 그 앱을 이번 패스에서 건너뛴다."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


# 있으면 매핑이어야 하는 최상위 키 — 부르는 쪽이 모두 `.get` 으로 읽는다.
_MAPPING_KEYS = ("launch", "build", "permissions", "resources", "source", "mcp")


def merge_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 MergePatch(Target, Patch). 둘 다 건드리지 않고 새 값을 돌려준다."""
    if not isinstance(patch, dict):
        return copy.deepcopy(patch)
    result = dict(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def load_file(manifest_path: Path) -> Any:
    """``manifest.yaml`` 을 읽어 효과 매니페스트를 돌려준다 — 옆에 ``manifest.local.yaml`` 이 있으면 얹은 것.

    로컬 파일이 있는데 얹지 못하면 :class:`LocalOverrideError` 를 올린다 — 추적 값으로 돌지 않게(그 앱을 보류한다).
    """
    manifest, info = load_file_with_override(manifest_path)
    if info is not None and info.held:
        raise LocalOverrideError(info.path, info.error or "")
    return manifest


def load_file_with_override(manifest_path: Path) -> tuple[Any, LocalOverride | None]:
    """효과 매니페스트와, 로컬 파일을 어떻게 다뤘는지(로컬 파일이 없으면 None)를 함께 돌려준다.

    로컬 파일을 얹지 못했으면(``held``) 돌려주는 매니페스트는 추적 값이다 — 상태 화면 · 진단이 보여 주기만 하고 그것으로 돌지 않는다.
    도는 쪽은 :func:`load_file` 을 쓴다.
    """
    base = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    local_path = manifest_path.with_name(LOCAL_FILE)
    try:
        raw = local_path.read_bytes()
    except FileNotFoundError:
        if local_path.is_symlink():
            # 있던 파일을 가리키던 심링크가 대상을 잃었다 — '파일 없음' 으로 치면 추적 값으로 조용히 넘어간다.
            return base, _held(local_path, "가리키는 대상이 없는 심링크다", state="dangling")
        _forget(local_path)
        return base, None
    except OSError as exc:
        return base, _held(local_path, f"읽지 못했다({exc})", state=f"unreadable:{exc}")

    sig = hashlib.sha256(raw).hexdigest()[:16]
    try:
        patch = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        reason = " ".join(str(exc).split())
        return base, _held(local_path, f"YAML 로 읽히지 않는다({reason})", state=f"broken:{sig}")
    if not isinstance(patch, dict):
        kind = "빈 파일" if patch is None else type(patch).__name__
        return base, _held(local_path, f"최상위가 매핑이 아니다({kind})", state=f"broken:{sig}")
    if not isinstance(base, dict):
        # 추적 매니페스트부터 깨졌다 — 부르는 쪽이 그것을 따로 알린다(예전과 같다). 얹을 자리가 없다.
        return base, LocalOverride(path=local_path, applied=False,
                                   error="추적 매니페스트가 매핑이 아니라 얹지 않았다")

    patch = dict(patch)
    rejected: list[str] = []
    if "id" in patch:
        new_id = patch.pop("id")
        if new_id != base.get("id"):
            rejected.append("id")

    effective = merge_patch(base, patch)
    shape = _shape_error(base, patch, effective)
    if shape:
        return base, _held(local_path, f"얹은 결과의 모양이 틀렸다 — {shape}", state=f"shape:{sig}")
    changed = sorted(
        str(k) for k in set(base) | set(effective)
        if base.get(k, _MISSING) != effective.get(k, _MISSING)
    )
    info = LocalOverride(path=local_path, applied=True, changed_keys=changed, rejected_keys=rejected)
    if rejected:
        _note(local_path, f"applied:{sig}", logging.WARNING, repeat=True,
              msg=("manifest.local.yaml 의 id 바꾸기는 받지 않는다 — %s: id 는 추적 매니페스트의 %r 그대로다"
                   "(나머지는 얹었다, 바뀐 최상위 키 %s)"),
              args=(local_path, base.get("id"), changed))
    else:
        _note(local_path, f"applied:{sig}", logging.INFO, repeat=False,
              msg="manifest.local.yaml 을 얹었다 — %s: 바뀐 최상위 키 %s",
              args=(local_path, changed))
    return effective, info


def _held(local_path: Path, reason: str, *, state: str) -> LocalOverride:
    _note(local_path, state, logging.WARNING, repeat=True,
          msg=("manifest.local.yaml 을 얹지 못해 이 앱을 보류한다 — %s: %s. 고칠 때까지 라우트 · 기동 · 스냅숏을 마지막 상태 그대로 두고"
               " 추적 값(manifest.yaml)으로 돌지 않는다"),
          args=(local_path, reason))
    return LocalOverride(path=local_path, applied=False, error=reason, held=True)


def _shape_error(base: dict, patch: dict, effective: dict) -> str | None:
    """얹은 결과를 허브가 그대로 읽을 수 있는지 — 아니면 까닭을 돌려준다(그 앱을 보류한다).

    YAML 로는 멀쩡한 실수를 잡는다. ``launch: http://…`` · ``launch: service`` 처럼 블록을 글자로 적은 것, 짧은 덮어쓰기에서
    들여쓰기를 하나 빠뜨려 ``launch:`` 가 null 로 읽힌 것(그 블록이 통째로 지워진다), 쓰는 도중에 읽혀 build 만 바뀌고
    launch 는 추적 값으로 남은 것(fastapi 스택에 proxy 모드).
    """
    for key in _MAPPING_KEYS:
        if key in patch and key in effective and not isinstance(effective[key], dict):
            return f"{key} 는 매핑이어야 한다(지금 {type(effective[key]).__name__})"
    for key, sub in (("launch", "mode"), ("build", "stack")):
        if not isinstance(base.get(key), dict):
            continue
        if key not in effective:
            return f"추적 매니페스트의 {key} 블록을 통째로 지운다(들여쓰기가 빠지면 '{key}:' 가 null 로 읽힌다)"
        if base[key].get(sub) and not effective[key].get(sub):
            return f"추적 매니페스트의 {key}.{sub} 를 지운다"
    if "launch" in patch or "build" in patch:
        mismatch = _stack_mode_mismatch(effective)
        if mismatch and not _stack_mode_mismatch(base):
            return mismatch
    return None


def _stack_mode_mismatch(manifest: dict) -> str | None:
    """build.stack 이 config/stacks.yaml 에 적힌 launch_mode 와 launch.mode 가 어긋나면 그 설명을 돌려준다."""
    build, launch = manifest.get("build"), manifest.get("launch")
    if not isinstance(build, dict) or not isinstance(launch, dict):
        return None
    stack, mode = build.get("stack"), launch.get("mode")
    if not stack or not mode:
        return None
    try:
        from app.services import stack_resolver  # noqa: PLC0415 — 스택 표는 첫 호출 뒤 캐시된다

        spec = stack_resolver.load_stacks().get(str(stack))
    except Exception:  # noqa: BLE001 — 스택 표를 못 읽으면 이 검사만 건너뛴다(모르는 스택은 스캐너가 따로 알린다)
        return None
    if spec is None or spec.launch_mode == mode:
        return None
    return (f"build.stack {stack} 는 launch.mode {spec.launch_mode} 인 스택인데 launch.mode 가 {mode!r} 다"
            "(모드를 바꾸면 스택도 함께 바꾼다)")


def _note(local_path: Path, state: str, level: int, *, repeat: bool, msg: str, args: tuple) -> None:
    """같은 상태는 한 번만 알린다(repeat 이면 한 시간마다 다시)."""
    key = str(local_path)
    now = time.monotonic()
    prev = _NOTED.get(key)
    if prev and prev[0] == state and (not repeat or now - prev[1] < _WARN_REPEAT_S):
        return
    _NOTED[key] = (state, now)
    logger.log(level, msg, *args)


def _forget(local_path: Path) -> None:
    """있던 로컬 파일이 없어졌으면 한 번 알린다 — 지운 것이 다음 패스에 반영됐다는 흔적이다."""
    if _NOTED.pop(str(local_path), None) is not None:
        logger.info("manifest.local.yaml 이 없어졌다 — %s: 추적 매니페스트(manifest.yaml)만으로 돈다", local_path)
