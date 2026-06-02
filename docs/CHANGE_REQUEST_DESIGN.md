# Change Request Design — AI 기반 manifest 제안서 자동화

## 1. 목적

레거시 / 외부 GitHub repo를 HEAXHub 포탈에 등록할 때:

1. **upstream 소스 코드는 절대 수정하지 않는다.**
2. AI가 repo를 분석해 `.portal/manifest.yaml` + `.portal/run.sh` 등 **추가만 하는** 변경 명세서를 생성한다.
3. 운영자가 검토한 뒤 GitHub PR을 자동으로 발행하거나 Markdown 명세서를 다운로드한다.
4. 개발자가 PR을 merge하면 webhook으로 포탈이 자동 동기화한다.

## 2. 3-Stage 파이프라인

```text
1) Static Analyzer (services/static_analyzer.py)
   결정론적, 빠름, 환각 없음
   - 언어 감지, 버전 파일 읽기, package.json 파싱, env 키 추출

2) Manifest LLM (services/manifest_llm.py)
   Claude/GPT/사내 LLM 호출, JSON-only 응답
   - manifest_draft, confidence, open_questions, developer_change_request

3) Change Request Builder (services/change_request.py)
   결정론적 포맷터
   - Markdown 명세서, JSON 패치, GitHub PR payload
```

## 3. 파일 분담

| 파일 | 책임 |
|---|---|
| `services/static_analyzer.py` | repo 워크스페이스를 결정론적으로 분석 |
| `services/manifest_llm.py` | LLM 호출 + JSON schema 검증 + 재시도 |
| `services/change_request.py` | Markdown + JSON + PR payload 생성 |
| `services/github_integration.py` | PyGithub fork/branch/file/PR API |
| `runners/llm_provider.py` | Anthropic/OpenAI/local 어댑터 |
| `api/v1/change_requests.py` | 운영자 검토 + 발행 endpoint |
| `db/models/change_request.py` | 발행 이력 추적 |

## 4. Static Analyzer 사양

```python
@dataclass
class StaticFacts:
    languages: list[str]                       # ["python", "nodejs"]
    python_version: str | None                 # "3.11"
    python_version_source: str | None          # ".python-version", "pyproject.toml" 등
    node_version: str | None
    package_json_scripts: dict[str, str]
    has_dockerfile: bool
    has_apptainer_def: bool
    has_compose_yaml: bool
    detected_env_references: list[str]          # ["DATABASE_URL", "JWT_SECRET", ...]
    daemon_indicators: list[str]                # ["uvicorn", "streamlit"]
    gpu_libs: list[str]                         # ["torch", "tensorflow"]
    license_keywords: list[str]                 # ["lsdyna", "ansys"]
    has_alembic_ini: bool
    has_prisma_schema: bool
    repo_size_bytes: int
    entry_files: list[str]                      # ["app/main.py"]
    github_workflows: list[str]
    readme_run_commands: list[str]              # README에서 추출
```

다음 함수들로 구성:

- `detect_languages(workspace)` — 파일 확장자/시그니처 기반
- `read_python_version(workspace)` — 5가지 경로 순서대로 확인
- `read_node_version(workspace)` — 3가지 경로
- `extract_env_references(workspace)` — `os.environ.get('...')`, `process.env.X`, `dotenv` 등 grep
- `detect_daemon_pattern(workspace)` — 데몬 명령 키워드 발견
- `needs_gpu(workspace)` — GPU 라이브러리 키워드
- `extract_readme_commands(workspace)` — `## How to run` 섹션의 코드 블록

## 5. Manifest LLM 사양

### 5.1 입력 컨텍스트 구성

```python
def build_llm_context(workspace: Path, facts: StaticFacts) -> dict:
    blobs = {}
    for f in [
        "README.md", "README.rst", "README",
        "package.json", "pyproject.toml", "requirements.txt",
        "Dockerfile", "Apptainer.def", "docker-compose.yml",
        ".github/workflows/release.yml", ".github/workflows/build.yml",
        "Makefile",
    ]:
        p = workspace / "upstream" / f
        if p.exists() and p.stat().st_size < 50_000:
            blobs[f] = p.read_text(errors='replace')
    # 엔트리포인트만 추가 — 코드 본문은 토큰 낭비
    for entry in facts.entry_files[:2]:
        p = workspace / "upstream" / entry
        if p.exists() and p.stat().st_size < 30_000:
            blobs[entry] = p.read_text(errors='replace')
    return {"static_facts": asdict(facts), "files": blobs}
```

### 5.2 시스템 프롬프트 (요약)

```text
You analyze a software repository and produce a JSON output for HEAXHub.

Output shape (strict):
{
  "manifest_draft": { ... yaml-serializable per HEAXHub schema v2 },
  "confidence": { "<field path>": 0.0-1.0 },
  "open_questions": [
    { "field": "...", "question": "...", "candidates": [...], "context": "..." }
  ],
  "developer_change_request": {
    "summary": "...",
    "required_files": [
      { "path": ".portal/manifest.yaml", "kind": "create", "content": "..." },
      { "path": ".portal/run.sh", "kind": "create", "content": "...", "mode": "0755" }
    ],
    "suggested_files": [
      { "path": "README.md", "kind": "append", "section": "...", "content": "..." }
    ],
    "rationale": "..."
  }
}

Hard rules:
- 절대 upstream 소스 코드는 수정 제안 금지. .portal/ 디렉터리 추가만 허용.
- required_files[].path 가 .portal/로 시작하지 않으면 reject (caller가 검증).
- confidence < 0.7 인 항목은 manifest_draft 에서 제외하고 open_questions 에 넣어라.
- 한국어로 작성, 코드는 영문 유지.
- README의 명령을 그대로 신뢰하지 말고 어떤 명령이 데몬형/배치형인지 분류해라.
```

### 5.3 응답 검증 + 재시도

```python
async def call_llm(context: dict, max_retries: int = 3) -> LLMResult:
    for attempt in range(max_retries):
        response = await llm_provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(context),
            response_format="json",
        )
        try:
            payload = json.loads(response)
            jsonschema.validate(payload, LLM_RESPONSE_SCHEMA)
            _validate_no_upstream_modifications(payload)
            return LLMResult.parse(payload)
        except (json.JSONDecodeError, jsonschema.ValidationError, ValueError) as e:
            logger.warning("LLM response invalid (attempt %d): %s", attempt + 1, e)
            if attempt == max_retries - 1:
                raise
    raise RuntimeError("unreachable")


def _validate_no_upstream_modifications(payload: dict) -> None:
    for f in payload.get("developer_change_request", {}).get("required_files", []):
        if not f["path"].startswith(".portal/") and f["path"] not in {"README.md"}:
            raise ValueError(f"Unsafe path: {f['path']}")
```

### 5.4 LLM Provider 어댑터

```python
class BaseLLMProvider:
    async def complete(self, *, system: str, user: str, response_format: str) -> str: ...

class AnthropicProvider(BaseLLMProvider): ...
class OpenAIProvider(BaseLLMProvider): ...
class LocalLLMProvider(BaseLLMProvider): ...  # 사내 게이트웨이

def get_provider() -> BaseLLMProvider:
    settings = get_settings()
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.llm_api_key, model=settings.llm_model)
    if settings.llm_provider == "openai":
        return OpenAIProvider(api_key=settings.llm_api_key, model=settings.llm_model)
    if settings.llm_provider == "local":
        return LocalLLMProvider(base_url=settings.llm_local_endpoint)
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")
```

## 6. Change Request Builder

```python
@dataclass
class ChangeRequest:
    id: UUID
    submission_id: UUID | None
    app_id: str | None
    repo_url: str
    commit_sha: str
    static_facts: dict
    llm_result: LLMResult
    operator_overrides: dict           # 운영자 검토 결과
    final_manifest: dict
    markdown_body: str
    pr_payload: dict | None
    status: str                        # draft | issued_md | issued_pr | merged | rejected | superseded
    issued_at: datetime | None
    merged_at: datetime | None


def build_change_request(
    *,
    submission: Submission,
    static_facts: StaticFacts,
    llm_result: LLMResult,
    operator_overrides: dict | None = None,
) -> ChangeRequest:
    overrides = operator_overrides or {}
    final_manifest = deep_merge(llm_result.manifest_draft, overrides.get("manifest", {}))

    md_body = render_markdown(submission, final_manifest, llm_result, overrides)
    pr_payload = render_pr_payload(submission, final_manifest, llm_result.developer_change_request)

    return ChangeRequest(
        id=uuid.uuid4(),
        submission_id=submission.id,
        app_id=submission.proposed_app_id,
        repo_url=str(submission.upstream_repo_url),
        commit_sha=static_facts.commit_sha,
        static_facts=asdict(static_facts),
        llm_result=llm_result,
        operator_overrides=overrides,
        final_manifest=final_manifest,
        markdown_body=md_body,
        pr_payload=pr_payload,
        status="draft",
        issued_at=None,
        merged_at=None,
    )
```

## 7. Markdown 명세서 템플릿

```markdown
# HEAXHub 포탈 등록 요청 — {app_name}

안녕하세요. 사내 자동화 포탈 운영팀입니다.
`{app_name}` 앱을 HEAXHub 포탈에 등록하기 위해 다음 변경을 부탁드립니다.

**기존 소스 코드는 건드릴 필요가 없습니다.** 모든 추가 파일은 `.portal/` 디렉터리에만 들어갑니다.

## 1. 추가할 파일

### `.portal/manifest.yaml` (신규)
```yaml
{manifest_yaml}
```

### `.portal/run.sh` (신규, 실행 권한 0755)
```bash
{run_script}
```

## 2. (선택) README에 추가 권장
{readme_suggestion}

## 3. 확인이 필요한 항목
{open_questions_table}

## 4. 적용 후
위 파일들을 PR로 merge 후 `git tag v{version} && git push --tags` 를 실행하시면
포탈이 자동으로 새 버전을 검토 대기열에 올립니다.

문의: heaxhub-operators@company.com
```

## 8. GitHub Integration

### 8.1 PR 자동 생성

```python
def publish_pr(*, change_request: ChangeRequest, repo_url: str, bot_token: str) -> str:
    gh = github.Github(bot_token)
    owner, name = parse_github_url(repo_url)
    upstream = gh.get_repo(f"{owner}/{name}")

    # Fork (운영자 봇 계정)
    fork = upstream.create_fork()
    _wait_for_fork(fork)

    # Branch
    base_sha = fork.get_branch(fork.default_branch).commit.sha
    branch = f"heaxhub/portal-registration-{change_request.id[:8]}"
    fork.create_git_ref(ref=f"refs/heads/{branch}", sha=base_sha)

    # Files
    for f in change_request.pr_payload["required_files"]:
        fork.create_file(
            path=f["path"],
            message=f"chore(portal): add {f['path']}",
            content=f["content"],
            branch=branch,
        )

    # PR
    pr = upstream.create_pull(
        title=f"HEAXHub 포탈 등록 — .portal/ 디렉터리 추가",
        body=change_request.markdown_body,
        head=f"{fork.owner.login}:{branch}",
        base=upstream.default_branch,
    )
    return pr.html_url
```

### 8.2 권한이 없는 경우 fallback

```python
def publish_issue(*, change_request: ChangeRequest, repo_url: str, bot_token: str) -> str:
    """PR 권한이 없을 때 Issue로 명세서만 전달."""
    gh = github.Github(bot_token)
    owner, name = parse_github_url(repo_url)
    repo = gh.get_repo(f"{owner}/{name}")
    issue = repo.create_issue(
        title="HEAXHub 포탈 등록 요청",
        body=change_request.markdown_body,
        labels=["heaxhub", "documentation"],
    )
    return issue.html_url
```

### 8.3 Markdown 다운로드

```python
def render_markdown_attachment(change_request: ChangeRequest) -> bytes:
    return change_request.markdown_body.encode("utf-8")
```

운영자 UI에서 `다운로드` 버튼 누르면 `.md` 파일 받기.

## 9. 데이터베이스 — `change_requests` 테이블

```sql
CREATE TABLE change_requests (
  id              UUID PRIMARY KEY,
  submission_id   UUID REFERENCES submissions(id) ON DELETE SET NULL,
  app_id          VARCHAR(64) REFERENCES apps(id) ON DELETE SET NULL,
  repo_url        TEXT NOT NULL,
  commit_sha      TEXT,
  static_facts    JSONB NOT NULL,
  llm_response    JSONB NOT NULL,
  operator_overrides JSONB DEFAULT '{}'::jsonb,
  final_manifest  JSONB NOT NULL,
  markdown_body   TEXT NOT NULL,
  pr_payload      JSONB,
  status          TEXT NOT NULL DEFAULT 'draft',
  pr_url          TEXT,
  issued_at       TIMESTAMPTZ,
  merged_at       TIMESTAMPTZ,
  created_by      UUID REFERENCES users(id),
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ix_change_requests_status ON change_requests(status);
CREATE INDEX ix_change_requests_submission ON change_requests(submission_id);
```

## 10. API endpoints

```text
POST   /api/v1/change-requests                          # static + LLM 실행, draft 생성
GET    /api/v1/change-requests/{id}                     # 운영자 검토용 전체 데이터
PATCH  /api/v1/change-requests/{id}                     # operator_overrides 갱신, final_manifest 재계산
POST   /api/v1/change-requests/{id}/issue?via=pr|issue|markdown
                                                        # 발행
GET    /api/v1/change-requests/{id}/markdown            # 다운로드
POST   /api/v1/webhooks/github/pr                       # PR 상태 변경 수신 → merged_at 갱신
```

## 11. 운영자 UI

`/admin/change-requests`:

- 목록 — 상태별 필터, 최근 발행순
- 상세 — 3단 컬럼 (정적/AI/운영자), 신뢰도 색상 코딩
  - 빨강 (< 0.7): 운영자 입력 필수
  - 노랑 (0.7~0.9): 검토 필요
  - 초록 (≥ 0.9): 자동 채택 후보
- 발행 버튼 — `PR` / `Issue` / `Markdown 다운로드` 3개

## 12. 환경 변수

```bash
# LLM
LLM_PROVIDER=anthropic | openai | local
LLM_API_KEY=
LLM_MODEL=claude-sonnet-4-5
LLM_TEMPERATURE=0.0
LLM_MAX_TOKENS=8000
LLM_LOCAL_ENDPOINT=             # provider=local 시 사용

# GitHub
GITHUB_BOT_TOKEN=
GITHUB_BOT_USERNAME=heaxhub-bot
INTEGRATION_REPO_URL=https://github.com/squall321/MXCAEGroupAutomationSample
```

## 13. 안전장치 요약

| 위험 | 방어 |
|---|---|
| LLM 환각으로 잘못된 manifest | confidence < 0.8 자동 채택 금지 |
| upstream 코드 수정 제안 | required_files[].path 검증 |
| 자동 merge로 사고 | PR만 생성, merge는 사람만 |
| API 토큰 노출 | secret_manager로 봇 토큰 격리 |
| LLM JSON 깨짐 | schema 검증 + 3회 재시도 |
| 동일 commit 중복 호출 | static_facts hash 기반 Redis 캐시 |
| 사내망 외부 LLM 차단 | local provider 옵션 |

## 14. 향후 확장

- **자동 명세서 학습** — 운영자 수정 패턴을 데이터셋으로 모아 사내 LLM fine-tune
- **음성 명세서** — 운영자가 음성으로 확인 사항 추가
- **알림** — PR이 merge되면 신청자/운영자에게 슬랙 알림
- **multi-repo 일괄 발행** — 동일 명세서 패턴을 여러 repo에 한 번에
