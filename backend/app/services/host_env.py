# 허브가 앱을 띄울 때마다 리포 루트 .env 를 새로 읽어 허브 프로세스 env 위에 얹은 호스트 env 를 만든다
"""Host env for app launches — the hub process env refreshed from ``$ROOT/.env``.

start.sh 는 허브(uvicorn · celery)를 띄울 때 ``.env`` 를 한 번 source 한다. 그 뒤 운영자가 ``.env`` 를
고쳐도 허브 프로세스 env 는 옛 값 그대로라, 자동 길(reconcile · 스캐너 전환 · 죽은 앱 되살리기)로 뜨는
앱은 옛 ``APPTAINERENV_*`` · 프록시 · LLM 값을 받았다(cae00 HH-18 — 11-12 DocForge 코어 주소 · NO_PROXY
를 바꾼 뒤 자동 길로 뜬 앱이 이미 거부되는 옛 주소로 돌아갔다). 손 길(redeploy-app.sh)만 .env 를 새로 읽었다.

그래서 앱을 띄울 때마다 :func:`current` 로 .env 를 다시 읽는다.

* .env 값이 프로세스 값을 이긴다(새로 source 한 것과 같다).
* .env 에 없는 ``APPTAINERENV_*`` 는 뺀다 — 그 키들은 .env 에서만 오므로, 없으면 운영자가 지운 것이다.
* 그 밖의 키(프록시 · LLM · 검색)는 .env 에 없으면 프로세스 값을 둔다 — 셸 프로필에서 export 한 박스가 있다.
* .env 가 없으면 프로세스 env 를 그대로 돌려준다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

# start.sh · redeploy-app.sh 가 읽는 바로 그 파일.
ENV_FILE: Path = Path(__file__).resolve().parents[3] / ".env"

_APPT_PREFIX = "APPTAINERENV_"


def current() -> dict[str, str]:
    """허브 프로세스 env 에 지금의 ``.env`` 를 얹은 사본."""
    env = dict(os.environ)
    if not ENV_FILE.is_file():
        return env
    # 값 없는 줄(``KEY`` 만)은 None 으로 온다 — bash source 처럼 아무것도 정하지 않는다.
    values = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}
    for k in [k for k in env if k.startswith(_APPT_PREFIX) and k not in values]:
        del env[k]
    env.update(values)
    return env
