"""HEAXHub Dash 데모 대시보드.

Dash 의 시그니처 reactive 패턴 (`@callback` 으로 컨트롤 → 그래프 갱신) 을
보여주는 단일 파일 앱. 분포/샘플 수/색상을 선택하면 히스토그램·산점도가
동시에 다시 그려지고, 요약 통계 테이블이 갱신된다.

HEAXHub 서비스 모드에서는 환경변수 `HEAX_BASE_PATH` 가 주입되며
(`$ROOT_PATH` 와 호환), Dash 의 `requests_pathname_prefix` / `routes_pathname_prefix`
로 전달해 Caddy reverse proxy 의 sub-path 마운트에서도 정상 동작한다.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import plotly.express as px
from dash import Dash, Input, Output, callback, dash_table, dcc, html

# ---------------------------------------------------------------------------
# Palette (HEAXHub)
# ---------------------------------------------------------------------------
PALETTE = {
    "bg": "#0f172a",       # slate-900
    "panel": "#1e293b",    # slate-800
    "accent": "#38bdf8",   # sky-400
    "text": "#f1f5f9",     # slate-100
    "muted": "#94a3b8",    # slate-400
    "border": "#334155",   # slate-700
}

COLOR_CHOICES = [
    {"label": "Sky", "value": "#38bdf8"},
    {"label": "Emerald", "value": "#34d399"},
    {"label": "Amber", "value": "#fbbf24"},
    {"label": "Rose", "value": "#f43f5e"},
    {"label": "Violet", "value": "#a78bfa"},
]

DISTRIBUTIONS = [
    {"label": "Normal", "value": "normal"},
    {"label": "Uniform", "value": "uniform"},
    {"label": "Exponential", "value": "exponential"},
]


# ---------------------------------------------------------------------------
# Base path handling for reverse proxy
# ---------------------------------------------------------------------------
def _normalize_base_path(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw or raw == "/":
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    if not raw.endswith("/"):
        raw = raw + "/"
    return raw


BASE_PATH = _normalize_base_path(
    os.environ.get("HEAX_BASE_PATH", "") or os.environ.get("ROOT_PATH", "")
)


# ---------------------------------------------------------------------------
# Dash app
# ---------------------------------------------------------------------------
app_kwargs: dict[str, object] = {"title": "HEAXHub Dash Demo"}
if BASE_PATH != "/":
    app_kwargs["requests_pathname_prefix"] = BASE_PATH
    app_kwargs["routes_pathname_prefix"] = BASE_PATH

app = Dash(__name__, **app_kwargs)
server = app.server  # gunicorn / uvicorn workers can target this


# ---------------------------------------------------------------------------
# Styles (inline, no external CSS)
# ---------------------------------------------------------------------------
PAGE_STYLE = {
    "backgroundColor": PALETTE["bg"],
    "color": PALETTE["text"],
    "minHeight": "100vh",
    "padding": "24px",
    "fontFamily": "system-ui, -apple-system, Segoe UI, sans-serif",
}

HEADER_STYLE = {
    "padding": "20px 24px",
    "borderRadius": "12px",
    "background": f"linear-gradient(90deg, {PALETTE['panel']} 0%, {PALETTE['bg']} 100%)",
    "border": f"1px solid {PALETTE['border']}",
    "marginBottom": "20px",
}

CONTROL_PANEL_STYLE = {
    "backgroundColor": PALETTE["panel"],
    "padding": "20px",
    "borderRadius": "12px",
    "border": f"1px solid {PALETTE['border']}",
    "marginBottom": "20px",
}

GRAPH_CARD_STYLE = {
    "backgroundColor": PALETTE["panel"],
    "padding": "16px",
    "borderRadius": "12px",
    "border": f"1px solid {PALETTE['border']}",
    "flex": "1 1 0",
    "minWidth": "0",
}

ROW_STYLE = {"display": "flex", "gap": "16px", "marginBottom": "20px", "flexWrap": "wrap"}

LABEL_STYLE = {"color": PALETTE["muted"], "fontSize": "13px", "marginBottom": "6px"}


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
app.layout = html.Div(
    style=PAGE_STYLE,
    children=[
        html.Div(
            style=HEADER_STYLE,
            children=[
                html.H1(
                    "HEAXHub Dash 데모 대시보드",
                    style={"margin": 0, "color": PALETTE["text"], "fontSize": "24px"},
                ),
                html.Div(
                    "Dash @callback 시그니처 패턴 — 컨트롤 한 번에 두 그래프 + 통계 테이블 동시 갱신",
                    style={"color": PALETTE["muted"], "fontSize": "13px", "marginTop": "6px"},
                ),
                html.Div(
                    f"base path: {BASE_PATH}",
                    style={"color": PALETTE["accent"], "fontSize": "12px", "marginTop": "4px"},
                ),
            ],
        ),
        html.Div(
            style=CONTROL_PANEL_STYLE,
            children=[
                html.Div(
                    style={"display": "grid", "gridTemplateColumns": "2fr 1fr 1fr", "gap": "24px"},
                    children=[
                        html.Div(
                            children=[
                                html.Div("샘플 수 N (10 ~ 2000)", style=LABEL_STYLE),
                                dcc.Slider(
                                    id="n-slider",
                                    min=10,
                                    max=2000,
                                    step=10,
                                    value=500,
                                    marks={10: "10", 500: "500", 1000: "1000", 2000: "2000"},
                                    tooltip={"placement": "bottom", "always_visible": False},
                                ),
                            ]
                        ),
                        html.Div(
                            children=[
                                html.Div("분포", style=LABEL_STYLE),
                                dcc.RadioItems(
                                    id="dist-radio",
                                    options=DISTRIBUTIONS,
                                    value="normal",
                                    inline=True,
                                    labelStyle={"marginRight": "12px", "color": PALETTE["text"]},
                                ),
                            ]
                        ),
                        html.Div(
                            children=[
                                html.Div("색상", style=LABEL_STYLE),
                                dcc.Dropdown(
                                    id="color-dropdown",
                                    options=COLOR_CHOICES,
                                    value=PALETTE["accent"],
                                    clearable=False,
                                    style={"color": "#0f172a"},
                                ),
                            ]
                        ),
                    ],
                ),
            ],
        ),
        html.Div(
            style=ROW_STYLE,
            children=[
                html.Div(
                    style=GRAPH_CARD_STYLE,
                    children=[
                        html.Div("히스토그램", style={"color": PALETTE["muted"], "marginBottom": "8px"}),
                        dcc.Graph(id="hist-graph", config={"displayModeBar": False}),
                    ],
                ),
                html.Div(
                    style=GRAPH_CARD_STYLE,
                    children=[
                        html.Div("산점도 (x = sample, y = sample shifted)", style={"color": PALETTE["muted"], "marginBottom": "8px"}),
                        dcc.Graph(id="scatter-graph", config={"displayModeBar": False}),
                    ],
                ),
            ],
        ),
        html.Div(
            style={**CONTROL_PANEL_STYLE, "marginBottom": 0},
            children=[
                html.Div("요약 통계", style={"color": PALETTE["muted"], "marginBottom": "8px"}),
                dash_table.DataTable(
                    id="stats-table",
                    columns=[
                        {"name": "metric", "id": "metric"},
                        {"name": "value", "id": "value"},
                    ],
                    style_header={
                        "backgroundColor": PALETTE["bg"],
                        "color": PALETTE["text"],
                        "border": f"1px solid {PALETTE['border']}",
                    },
                    style_cell={
                        "backgroundColor": PALETTE["panel"],
                        "color": PALETTE["text"],
                        "border": f"1px solid {PALETTE['border']}",
                        "padding": "8px 12px",
                        "fontFamily": "ui-monospace, SFMono-Regular, Menlo, monospace",
                    },
                ),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Sample generation
# ---------------------------------------------------------------------------
def _draw(n: int, dist: str) -> np.ndarray:
    rng = np.random.default_rng(42)
    n = max(10, min(int(n), 2000))
    if dist == "uniform":
        return rng.uniform(low=-3.0, high=3.0, size=n)
    if dist == "exponential":
        return rng.exponential(scale=1.0, size=n)
    return rng.normal(loc=0.0, scale=1.0, size=n)


# ---------------------------------------------------------------------------
# Reactive callback (Dash signature pattern):
# one callback fans out to histogram + scatter + summary table
# ---------------------------------------------------------------------------
@callback(
    Output("hist-graph", "figure"),
    Output("scatter-graph", "figure"),
    Output("stats-table", "data"),
    Input("n-slider", "value"),
    Input("dist-radio", "value"),
    Input("color-dropdown", "value"),
)
def update_views(n: int, dist: str, color: str):
    samples = _draw(n, dist)
    df = pd.DataFrame({"value": samples, "shifted": samples + 1.0})

    hist_fig = px.histogram(df, x="value", nbins=40, color_discrete_sequence=[color])
    hist_fig.update_layout(
        paper_bgcolor=PALETTE["panel"],
        plot_bgcolor=PALETTE["panel"],
        font_color=PALETTE["text"],
        margin={"l": 40, "r": 20, "t": 20, "b": 40},
        xaxis={"gridcolor": PALETTE["border"]},
        yaxis={"gridcolor": PALETTE["border"]},
    )

    scatter_fig = px.scatter(df, x="value", y="shifted", color_discrete_sequence=[color])
    scatter_fig.update_traces(marker={"size": 6, "opacity": 0.7})
    scatter_fig.update_layout(
        paper_bgcolor=PALETTE["panel"],
        plot_bgcolor=PALETTE["panel"],
        font_color=PALETTE["text"],
        margin={"l": 40, "r": 20, "t": 20, "b": 40},
        xaxis={"gridcolor": PALETTE["border"]},
        yaxis={"gridcolor": PALETTE["border"]},
    )

    stats_rows = [
        {"metric": "n", "value": f"{len(samples):,}"},
        {"metric": "mean", "value": f"{samples.mean():.4f}"},
        {"metric": "std", "value": f"{samples.std(ddof=1):.4f}"},
        {"metric": "min", "value": f"{samples.min():.4f}"},
        {"metric": "max", "value": f"{samples.max():.4f}"},
    ]
    return hist_fig, scatter_fig, stats_rows


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8050"))
    app.run(host="0.0.0.0", port=port, debug=False)
