# Secret Rotation

두 종류의 시크릿이 `.env`(루트 + `backend/`)에 들어 있습니다. 둘 다 **로테이션 후 uvicorn 재시작 필요**.

## 1. JWT_SECRET (access/refresh 서명)

```bash
NEW=$(openssl rand -hex 64)
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW|" /home/koopark/claude/HEAXHub/.env
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW|" /home/koopark/claude/HEAXHub/backend/.env
apptainer instance stop heax-app && bash deploy/apptainer/start.sh
```

- 로테이션 직후 **기존 발급 토큰 전부 무효** — 모든 사용자 재로그인 필요.
- staging/production은 `local-dev-secret-…` 그대로면 부팅 시 `RuntimeError`. 배포 전 반드시 로테이션.

## 2. SECRET_ENCRYPTION_KEY (앱 시크릿 Fernet 암호화)

```bash
NEW=$(.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

**중요**: `secret_values` 테이블에 행이 1개라도 있으면 그대로 키를 바꾸면 복호화 실패. 반드시 다음 순서로 진행.

```bash
# 1) 현재 키로 모든 값을 평문으로 덤프
cd /home/koopark/claude/HEAXHub/backend
.venv/bin/python -m scripts.dump_secrets > /tmp/secrets.bak.json
# 2) 새 키로 .env 교체
sed -i "s|^SECRET_ENCRYPTION_KEY=.*|SECRET_ENCRYPTION_KEY=$NEW|" \
  /home/koopark/claude/HEAXHub/.env /home/koopark/claude/HEAXHub/backend/.env
# 3) uvicorn 재시작
apptainer instance stop heax-app && bash deploy/apptainer/start.sh
# 4) 새 키로 재암호화
.venv/bin/python -m scripts.reload_secrets /tmp/secrets.bak.json
rm /tmp/secrets.bak.json
```

빈 DB (행 0)면 1·4단계 생략 가능. 현재 호스트 상태 확인:

```bash
cd /home/koopark/claude/HEAXHub/backend && .venv/bin/python -c "
from app.db.session import SessionLocal; from sqlalchemy import text
db = SessionLocal()
print(db.execute(text('SELECT count(*) FROM secret_values')).scalar())
db.close()
"
```

## 점검

```bash
.venv/bin/python -c "from app.config import get_settings; s=get_settings(); \
  print('jwt_len=', len(s.jwt_secret)); print('fernet_set=', bool(s.secret_encryption_key))"
curl -sf http://localhost:4040/health
```

기대: `jwt_len=` ≥ 64, `fernet_set= True`, `/health` 200.
