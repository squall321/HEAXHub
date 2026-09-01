# Caddy 라이브 라우트에 업스트림 헤더(X-Heax-Gateway-Secret 등)가 실제로 실려 있는지 본다 — 설정만 있고 라우트에 안 구워진 경우를 가른다.
import json
import sys
import urllib.request

ADMIN = "http://127.0.0.1:2019"
WANT = sys.argv[1] if len(sys.argv) > 1 else None


def headers_of(route: dict) -> list[str]:
    """라우트(중첩 subroute 포함) 안 reverse_proxy 가 업스트림에 set 하는 헤더 이름들."""
    found: list[str] = []
    stack = list(route.get("handle", []))
    while stack:
        h = stack.pop()
        if h.get("handler") == "reverse_proxy":
            found += list(((h.get("headers") or {}).get("request") or {}).get("set", {}))
        for rr in h.get("routes", []) or []:
            stack += rr.get("handle", []) or []
    return found


def main() -> int:
    url = f"{ADMIN}/config/apps/http/servers/srv0/routes"
    try:
        with urllib.request.urlopen(url, timeout=6) as resp:      # noqa: S310 — 로컬 admin API.
            routes = json.load(resp)
    except Exception as exc:                                       # noqa: BLE001
        print(f"Caddy admin API 를 읽지 못했다: {exc}")
        return 2

    print(f"{'경로':34} {'forward_auth':13} {'gateway-secret':15} 헤더")
    for r in routes:
        paths = (r.get("match") or [{}])[0].get("path") or []
        first = paths[0] if paths else "(match 없음)"
        if WANT and WANT not in json.dumps(r):
            continue
        hs = headers_of(r)
        has_secret = any("gateway-secret" in x.lower() for x in hs)
        blob = json.dumps(r)
        fa = "있음" if "forward_auth" in blob else "없음"
        print(f"{first:34} {fa:13} {'있음' if has_secret else '없음':15} {','.join(hs)}")
    print()
    print("판정 — portal_auth 앱인데 gateway-secret 이 '없음' 이면 라우트가 시크릿 설정 이전에 구워진 것이다.")
    print("조치 — HEAXHub 백엔드를 재기동하거나 해당 앱을 재배포해 라우트를 다시 등록한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
