# 앱 하나(내부/외부 모두)를 HEAX 런타임에서 완전히 지운다 — Caddy 라우트·state·DB 행.
"""단일 앱 id 를 deprovision — Caddy 라우트, var/integration_state 파일, DB App 행 셋을 정리한다.

스캐너는 upsert-only(App 행을 절대 삭제 안 함, integrations_scanner.py 참고)라 매니페스트
디렉터리를 지워도 카탈로그에 앱이 그대로 남는다. 이 스크립트가 그 정식 제거 경로다.

순서(반드시 이 순서 — 매니페스트가 먼저 없어야 재조정 비트가 도로 살리지 않는다):
  1) integrations/<id>/ (또는 ext_<id>/) 매니페스트 디렉터리를 먼저 지운다(git rm 등, 이 스크립트 밖의 일).
  2) 이 스크립트로 라우트/state/DB 행을 정리한다.

DB 행 삭제는 App 을 참조하는 테이블 대부분이 ON DELETE CASCADE 라 자동 정리되지만,
``jobs.app_id`` 만 cascade 가 없어 걸린 job 이 있으면 삭제가 막힌다(job 이력 보존 의도로 추정
— 실수로 지우면 안 되는 데이터라 이 스크립트는 삭제 대신 ARCHIVED 로 전환하고 경고한다).
"""
from __future__ import annotations

import sys

from app.db.models.app import App, AppStatus
from app.db.models.job import Job
from app.db.session import SessionLocal
from app.services import integration_launcher as il
from app.services import proxy_manager


def deprovision(app_id: str, *, archive_if_blocked: bool = True) -> int:
    with SessionLocal() as db:
        app = db.get(App, app_id)

        route_res = proxy_manager.unregister_app_route(app_id=app_id)
        print(f"  · Caddy 라우트 해제: ok={getattr(route_res, 'ok', None)}")

        state_existed = il._state_path(app_id).exists()
        il._delete_state(app_id)
        print(f"  · state 파일: {'삭제됨' if state_existed else '원래 없음'}")

        if app is None:
            print(f"  · DB 행 없음(이미 제거됨) — id={app_id!r}")
            return 0

        njobs = db.query(Job).filter(Job.app_id == app_id).count()
        if njobs:
            if not archive_if_blocked:
                print(f"  ✗ DB 행 삭제 불가 — 참조 job {njobs}건 (jobs.app_id 는 cascade 없음)")
                return 1
            app.status = AppStatus.ARCHIVED
            db.commit()
            print(
                f"  ⚠ 참조 job {njobs}건 있어 DB 행을 지우지 않고 status=ARCHIVED 로 전환"
                "(카탈로그 목록엔 남되 job 이력은 보존됨)."
            )
            return 0

        db.delete(app)
        db.commit()
        print(f"  · DB 행 삭제 완료: id={app_id!r} (cascade: versions/permissions/favorites/service_instances)")
        return 0


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] in ("-h", "--help"):
        print("사용법: deprovision-app.sh <app_id>")
        print("  먼저 integrations/<app_id>/(또는 ext_<id>/) 매니페스트를 지운 뒤 실행하세요.")
        sys.exit(1 if len(sys.argv) != 2 else 0)
    app_id = sys.argv[1]
    manifest_hint_paths = [f"integrations/{app_id}", f"integrations/ext_{app_id}"]
    import os

    if any(os.path.isdir(p) for p in manifest_hint_paths if os.path.isdir(p)):
        print(
            f"  ⚠ 매니페스트 디렉터리가 아직 있습니다({[p for p in manifest_hint_paths if os.path.isdir(p)]}). "
            "재조정 비트(≤45s)가 라우트를 되살릴 수 있습니다 — 먼저 git rm 하세요."
        )
    sys.exit(deprovision(app_id))


if __name__ == "__main__":
    main()
