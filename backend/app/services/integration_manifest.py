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
* 로컬 파일이 깨졌거나(YAML 오류 · 매핑이 아님) 읽히지 않으면 통째로 무시하고 추적 매니페스트로 돈다(WARNING).
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
    error: str | None = None                                # 통째로 무시한 까닭


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
    """``manifest.yaml`` 을 읽어 효과 매니페스트를 돌려준다 — 옆에 ``manifest.local.yaml`` 이 있으면 얹은 것."""
    return load_file_with_override(manifest_path)[0]


def load_file_with_override(manifest_path: Path) -> tuple[Any, LocalOverride | None]:
    """효과 매니페스트와, 로컬 파일을 어떻게 다뤘는지(로컬 파일이 없으면 None)를 함께 돌려준다."""
    base = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    local_path = manifest_path.with_name(LOCAL_FILE)
    try:
        raw = local_path.read_bytes()
    except FileNotFoundError:
        _forget(local_path)
        return base, None
    except OSError as exc:
        return base, _ignored(local_path, f"읽지 못했다({exc})", state=f"unreadable:{exc}")

    sig = hashlib.sha256(raw).hexdigest()[:16]
    try:
        patch = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        reason = " ".join(str(exc).split())
        return base, _ignored(local_path, f"YAML 로 읽히지 않는다({reason})", state=f"broken:{sig}")
    if not isinstance(patch, dict):
        kind = "빈 파일" if patch is None else type(patch).__name__
        return base, _ignored(local_path, f"최상위가 매핑이 아니다({kind})", state=f"broken:{sig}")
    if not isinstance(base, dict):
        # 추적 매니페스트부터 깨졌다 — 부르는 쪽이 그것을 따로 알린다. 얹을 자리가 없다.
        return base, LocalOverride(path=local_path, applied=False,
                                   error="추적 매니페스트가 매핑이 아니라 얹지 않았다")

    patch = dict(patch)
    rejected: list[str] = []
    if "id" in patch:
        new_id = patch.pop("id")
        if new_id != base.get("id"):
            rejected.append("id")

    effective = merge_patch(base, patch)
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


def _ignored(local_path: Path, reason: str, *, state: str) -> LocalOverride:
    _note(local_path, state, logging.WARNING, repeat=True,
          msg="manifest.local.yaml 을 무시한다 — %s: %s. 추적 매니페스트(manifest.yaml)만으로 돈다",
          args=(local_path, reason))
    return LocalOverride(path=local_path, applied=False, error=reason)


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
