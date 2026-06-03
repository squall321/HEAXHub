"""HEAXHub Streamlit 데모 대시보드.

NumPy 정규분포 샘플을 생성해 통계 요약, 히스토그램, 원본 데이터 head 를 보여준다.
HEAXHub 서비스 모드에서는 BASE_URL_PATH 환경변수 (또는 --server.baseUrlPath)
가 자동으로 적용되므로 reverse proxy 뒤에서도 정상 동작한다.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="HEAXHub Streamlit Demo",
    page_icon=None,
    layout="wide",
)

st.title("HEAXHub Streamlit 데모 대시보드")
st.caption("NumPy 정규분포 샘플 기반의 간단한 시각화 픽스처")

base_url_path = os.environ.get("BASE_URL_PATH", "") or os.environ.get("ROOT_PATH", "")
if base_url_path:
    st.info(f"현재 base URL path: `{base_url_path}` (reverse proxy 뒤에서 동작 중)")

# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("샘플 파라미터")
    sample_size = st.slider("샘플 수 N", min_value=200, max_value=5000, value=1000, step=100)
    mean = st.slider("평균 (mean)", min_value=-10.0, max_value=10.0, value=0.0, step=0.1)
    std = st.slider("표준편차 (std)", min_value=0.1, max_value=5.0, value=1.0, step=0.1)
    seed = st.number_input("난수 시드", min_value=0, max_value=2**31 - 1, value=42, step=1)
    bin_count = st.slider("히스토그램 bin 개수", min_value=10, max_value=100, value=30, step=1)

# ---------------------------------------------------------------------------
# Data generation
# ---------------------------------------------------------------------------
rng = np.random.default_rng(int(seed))
samples = rng.normal(loc=mean, scale=std, size=int(sample_size))
df = pd.DataFrame({"value": samples})

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------
st.subheader("통계 요약")
col1, col2, col3, col4 = st.columns(4)
col1.metric("샘플 수", f"{len(df):,}")
col2.metric("평균", f"{df['value'].mean():.4f}")
col3.metric("표준편차", f"{df['value'].std(ddof=1):.4f}")
col4.metric("범위", f"{df['value'].max() - df['value'].min():.4f}")

# ---------------------------------------------------------------------------
# Histogram
# ---------------------------------------------------------------------------
st.subheader("히스토그램")
counts, edges = np.histogram(samples, bins=int(bin_count))
centers = (edges[:-1] + edges[1:]) / 2.0
hist_df = pd.DataFrame({"count": counts}, index=pd.Index(np.round(centers, 4), name="bin_center"))
st.bar_chart(hist_df)

# ---------------------------------------------------------------------------
# Raw data preview
# ---------------------------------------------------------------------------
st.subheader("원본 데이터 head")
st.dataframe(df.head(20), use_container_width=True)
