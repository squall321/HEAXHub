# heax-demo-r

HEAXHub R 스택 데모. `r_script` 빌더로 실행되는 최소 통계 픽스처.

`run.R`은 표본 크기 `n`을 받아 `rnorm()`으로 표본을 만들고, 길이/평균/표준편차를
`output/result.txt`에 한 줄로 기록한다.

## 실행

```bash
Rscript run.R 200
cat output/result.txt
```

기본값은 `n = 100`이며, `set.seed(42)`로 재현성을 확보한다.

## 의존성

- R 4.x (`Rscript`) — base R만 사용한다.

## HEAXHub 통합

`.portal/manifest.yaml`이 `build.stack: r_script` / `launch.mode: job_runner`를
선언한다. 사용자가 폼에서 `n`을 입력하면 `Rscript run.R`이 실행되고
`output/` 산출물이 회수된다.
