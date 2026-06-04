# heax-demo-dash

HEAXHub Dash 스택 데모. Dash 의 시그니처 reactive 패턴 (`@callback` 데코레이터로
컨트롤 → 그래프 갱신) 을 보여주는 단일 파일 대시보드 픽스처.

## 구성

| 파일 | 역할 |
| --- | --- |
| `app.py` | Dash 단일 파일 앱 (layout + callback) |
| `pyproject.toml` | 패키지 메타 / 의존성 (`dash`, `plotly`, `numpy`, `pandas`) |

## 화면 구성

- 헤더 (`html.Div`): HEAXHub 팔레트 + 페이지 제목 + base path 표시
- 컨트롤 패널
  - `dcc.Slider` — 샘플 수 N (10 ~ 2000)
  - `dcc.RadioItems` — 분포 (normal / uniform / exponential)
  - `dcc.Dropdown` — 색상 (sky / emerald / amber / rose / violet)
- 두 개의 `dcc.Graph` 카드 (side-by-side)
  - 히스토그램 (선택 분포 + n 으로 즉시 갱신)
  - 산점도 (같은 샘플에서 계산)
- `dash_table.DataTable` 요약 통계 (n / mean / std / min / max)

## Reactive 패턴

```python
@callback(
    Output("hist-graph", "figure"),
    Output("scatter-graph", "figure"),
    Output("stats-table", "data"),
    Input("n-slider", "value"),
    Input("dist-radio", "value"),
    Input("color-dropdown", "value"),
)
def update_views(n, dist, color):
    ...
```

하나의 콜백이 3 개 Output (그래프 2개 + 테이블 1개) 을 한 번에 갱신한다.

## 로컬 실행

```bash
pip install -e .
python app.py
# → http://0.0.0.0:8050 에서 접속
```

## HEAXHub base path

reverse proxy (Caddy) 뒤에서 sub-path 마운트로 동작하려면 HEAXHub 런처가
환경변수 `HEAX_BASE_PATH` (또는 `ROOT_PATH`) 를 주입한다. `app.py` 는 시작
시점에 해당 변수를 읽어 Dash 의 `requests_pathname_prefix` /
`routes_pathname_prefix` 로 전달한다.

- `HEAX_BASE_PATH` 미설정 → `/` (개발 모드)
- `HEAX_BASE_PATH=/apps/heax_demo_dash` → `/apps/heax_demo_dash/`

## 의존성

- Python `>= 3.12`
- `dash >= 2.16`
- `plotly >= 5.20`
- `numpy >= 1.24`
- `pandas >= 2.0`

외부 CSS 없이 모든 스타일은 inline 으로 처리 (Bootstrap / dbc 의존성 없음).
