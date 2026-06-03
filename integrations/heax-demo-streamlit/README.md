# heax-demo-streamlit

HEAXHub Streamlit 스택 데모. NumPy 정규분포 샘플을 만들어 통계 요약, 히스토그램, 원본
데이터 head 를 한 화면에서 보여주는 대시보드 픽스처.

## 구성

| 파일 | 역할 |
| --- | --- |
| `.portal/manifest.yaml` | HEAXHub 매니페스트 (schema_version 2, `app_type: web_app`, `launch.mode: service`) |
| `app.py` | Streamlit 대시보드 본체 |
| `pyproject.toml` | 패키지 메타 / 의존성 (`streamlit>=1.28`, `numpy`, `pandas`) |

## 로컬 실행

```bash
pip install -e .
streamlit run app.py
```

## HEAXHub 매니페스트 요약

- `schema_version: 2`
- `id: heax_demo_streamlit`
- `app_type: web_app`, `execution_target: linux_runner`
- `build.stack: streamlit`
- `launch.mode: service`
- `launch.command`: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --server.baseUrlPath=$ROOT_PATH --browser.gatherUsageStats=false`
- `health_check.path: /_stcore/health`
- `restart_policy`: `on_failure`, `max_retries: 3`
- `resources`: CPU 1, Memory 1 GB
- `tags`: `[demo, streamlit, dashboard]`

HEAXHub 런처는 서비스 모드에서 `$PORT` 와 `$ROOT_PATH` 를 주입한다.
`--server.baseUrlPath` 가 reverse proxy 마운트 경로를 받기 때문에
`https://hub.example.com/apps/heax_demo_streamlit/` 같은 sub-path 마운트도 정상 동작한다.
편의를 위해 `app.py` 는 `BASE_URL_PATH` (또는 `ROOT_PATH`) 환경변수가 있으면 사이드바
상단에 안내 텍스트로 노출한다.

헬스체크는 Streamlit 내장 엔드포인트 `/_stcore/health` 를 사용한다.

## 대시보드 사용법

사이드바에서 다음 파라미터를 조정한다:

- `N` (200~5000): 샘플 수
- `mean` (-10~10): 정규분포 평균
- `std` (0.1~5.0): 표준편차
- `seed`: 난수 시드 (재현성)
- `bin 개수` (10~100): 히스토그램 bin 수

메인 패널에는 통계 요약 (`st.metric` 4 컬럼), 히스토그램 (`st.bar_chart`), 원본 데이터
head 20행 (`st.dataframe`) 이 차례로 표시된다.
