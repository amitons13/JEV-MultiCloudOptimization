"""Shared Streamlit UI helpers: theming, headers, badges, confidence bands."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from jev import ConfidenceBand
from jev.client import JevResponse

BAND_STYLE = {
    ConfidenceBand.HIGH: ("#ffffff", "#111111", "AUTO-REMEDIATE"),
    ConfidenceBand.MID: ("#111111", "#d4d4d4", "ESCALATE · HUMAN REVIEW"),
    ConfidenceBand.LOW: ("#111111", "#9a9a9a", "DEEP REASONING LLM"),
}

# Sober light palette (slate + indigo accent)
CLOUD_COLORS = {"Azure": "#4f5bd5", "AWS": "#e0a458", "GCP": "#3fa796"}
# cohesive muted sequence for categorical charts
GRAY_SEQ = ["#4f5bd5", "#3fa796", "#e0a458", "#8a74d6", "#5aa9e6", "#d98a9e", "#64748b", "#b5bd3f"]


def style_fig(fig, title: Optional[str] = None, height: Optional[int] = None):  # noqa: ANN001
    """Apply the sober light chart styling."""
    fig.update_layout(
        template="simple_white",
        font=dict(color="#1b2430", family="Inter, sans serif", size=13),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title=title,
        height=height,
        margin=dict(t=48 if title else 18, l=10, r=14, b=18),
    )
    fig.update_xaxes(gridcolor="#eceef3", zerolinecolor="#e0e3ea", linecolor="#d7dbe4")
    fig.update_yaxes(gridcolor="#eceef3", zerolinecolor="#e0e3ea", linecolor="#d7dbe4")
    return fig


def inject_css() -> None:
    st.markdown(
        """
        <style>
          @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

          /* ---- Design tokens (sober slate + indigo) ---- */
          :root {
            --ink:#1b2430; --ink-2:#3b4656; --muted:#6b7686; --faint:#9aa3b2;
            --line:#eef0f5; --line-2:#e5e8f0; --panel:#ffffff; --bg:#f6f7f9;
            --accent:#4f5bd5; --accent-hover:#3f49b8; --accent-soft:rgba(79,91,213,.10);
            --radius:18px; --shadow-sm:0 1px 2px rgba(27,36,48,.04), 0 1px 2px rgba(27,36,48,.04);
            --shadow-md:0 8px 24px rgba(27,36,48,.07), 0 2px 6px rgba(27,36,48,.04);
            --ease:cubic-bezier(.4,0,.2,1);
          }

          /* ---- Remove the left sidebar entirely ---- */
          [data-testid="stSidebar"],
          [data-testid="stSidebarCollapsedControl"],
          [data-testid="collapsedControl"] { display: none !important; }
          [data-testid="stAppViewContainer"] > .main { margin-left: 0 !important; }

          /* ---- Canvas & type ---- */
          html, body, [class*="css"], .stMarkdown, button, input, textarea, [data-baseweb] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
            font-feature-settings: "tnum", "cv01", "ss01";
          }
          [data-testid="stHeader"] { background: transparent; }
          .stApp { background: var(--bg); }
          .block-container { padding-top: 2.4rem; padding-bottom: 4rem; max-width: 1180px; }
          html, body, .stMarkdown, p, label { font-size: 15px; color: var(--ink-2); line-height: 1.7; }
          h1,h2,h3,h4 { letter-spacing: -0.02em; color: var(--ink); }
          h4 { font-weight: 700; margin-top: 1rem; margin-bottom: .5rem; }

          /* airier vertical rhythm between blocks */
          [data-testid="stVerticalBlock"] { gap: 1.25rem; }
          [data-testid="stElementContainer"] { margin-bottom: .15rem; }
          hr { margin: 1.4rem 0; border-color: var(--line); }
          a { color: var(--accent); }
          ::selection { background: var(--accent); color:#fff; }

          /* ---- Top brand bar ---- */
          .fg-topbar {
            display:flex; align-items:center; justify-content:space-between; gap:16px;
            background:var(--panel); border:1px solid var(--line-2); border-radius:22px;
            padding:20px 26px; margin-bottom:30px; box-shadow:var(--shadow-sm);
          }
          .fg-brand { display:flex; align-items:center; gap:15px; }
          .fg-logo {
            font-size:1.55rem; width:50px; height:50px; display:flex; align-items:center;
            justify-content:center; background:var(--accent); border-radius:15px;
            box-shadow:0 4px 12px rgba(79,91,213,.28);
          }
          .fg-title { font-size:1.4rem; font-weight:800; letter-spacing:-0.03em; line-height:1.05; color:var(--ink); }
          .fg-title span { position:relative; color:var(--accent); }
          .fg-title span::after {
            content:""; position:absolute; left:0; right:0; bottom:1px; height:6px;
            background:var(--accent-soft); z-index:-1; border-radius:3px;
          }
          .fg-sub { color:var(--muted); font-size:.88rem; margin-top:3px; font-weight:500; }
          .fg-status {
            display:inline-flex; align-items:center; gap:8px; padding:8px 15px; border-radius:999px;
            font-size:.8rem; font-weight:600; white-space:nowrap;
          }
          .fg-status.live { background:var(--accent); color:#fff; }
          .fg-dot { width:7px; height:7px; border-radius:50%; background:#fff;
            box-shadow:0 0 0 3px rgba(255,255,255,.22); }

          /* ---- Section banner ---- */
          .fg-banner { padding:4px 2px 20px; margin-bottom:28px; border-bottom:1px solid var(--line); }
          .fg-banner h1 { margin:0; font-size:1.7rem; font-weight:800; }
          .fg-banner p  { margin:10px 0 0; color:var(--muted); font-size:.98rem; max-width:940px;
            font-weight:450; line-height:1.65; }
          .fg-pill {
            display:inline-block; padding:5px 13px; border-radius:999px; font-size:.68rem;
            font-weight:700; margin-bottom:14px; letter-spacing:.06em; text-transform:uppercase;
            background:var(--accent-soft); color:var(--accent);
          }
          .fg-card { background:var(--panel); border:1px solid var(--line-2); border-radius:var(--radius);
            padding:22px 24px; height:100%; box-shadow:var(--shadow-sm); }

          /* ---- Tabs: segmented-control style ---- */
          .stTabs [data-baseweb="tab-list"] {
            gap:4px; background:#f0f0f3; padding:6px; border-radius:16px; border:none;
            width:fit-content; margin-bottom:16px;
          }
          .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display:none !important; }
          .stTabs [data-baseweb="tab"] {
            height:44px; padding:0 22px; font-weight:600; font-size:.98rem; color:var(--ink-2);
            border-radius:10px; background:transparent; transition:all .2s var(--ease);
          }
          .stTabs [data-baseweb="tab"] p { color:inherit !important; }
          .stTabs [data-baseweb="tab"]:hover { color:var(--ink); }
          .stTabs [aria-selected="true"] {
            color:var(--accent) !important; background:var(--panel); box-shadow:var(--shadow-sm);
          }

          /* ---- Metric cards ---- */
          div[data-testid="stMetric"] {
            background:var(--panel); border:1px solid var(--line-2); border-radius:var(--radius);
            padding:20px 22px; box-shadow:var(--shadow-sm); transition:all .2s var(--ease);
          }
          div[data-testid="stMetric"]:hover { box-shadow:var(--shadow-md); transform:translateY(-2px);
            border-color:#d6dae6; }
          div[data-testid="stMetricLabel"] p { color:var(--muted); font-size:.8rem; font-weight:500; }
          div[data-testid="stMetricValue"] { font-size:1.85rem; font-weight:800; color:var(--ink);
            letter-spacing:-0.02em; }

          /* ---- Buttons (fluid) ---- */
          .stButton > button, .stDownloadButton > button, .stFormSubmitButton > button,
          [data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-primary"] {
            border-radius:12px; font-weight:600; font-size:.98rem; padding:.6rem 1.35rem;
            border:1px solid var(--line-2); background:var(--panel); color:var(--ink);
            box-shadow:var(--shadow-sm); transition:all .18s var(--ease);
          }
          /* Streamlit renders the label in an inner element that keeps its own
             color — force it to inherit so button text is always visible. */
          .stButton > button *, .stDownloadButton > button *, .stFormSubmitButton > button *,
          [data-testid="stBaseButton-secondary"] *, [data-testid="stBaseButton-primary"] * {
            color: inherit !important; fill: currentColor !important;
          }
          .stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {
            border-color:var(--accent); color:var(--accent); background:var(--accent-soft);
            box-shadow:var(--shadow-md); transform:translateY(-1px);
          }
          .stButton > button:active, .stDownloadButton > button:active { transform:translateY(0); }
          .stButton > button:focus-visible, .stDownloadButton > button:focus-visible,
          .stFormSubmitButton > button:focus-visible {
            outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft);
          }
          /* Primary (filled) — white label on indigo, always readable */
          .stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"],
          [data-testid="stBaseButton-primary"] {
            background:var(--accent); color:#fff !important; border-color:var(--accent);
          }
          .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover,
          [data-testid="stBaseButton-primary"]:hover {
            background:var(--accent-hover); color:#fff !important; border-color:var(--accent-hover);
          }
          .stButton > button[kind="primary"] *, .stFormSubmitButton > button[kind="primary"] *,
          [data-testid="stBaseButton-primary"] * { color:#fff !important; }
          /* Disabled — dimmed but still legible */
          .stButton > button:disabled, .stDownloadButton > button:disabled {
            color:var(--faint) !important; background:var(--line); border-color:var(--line-2);
            box-shadow:none; opacity:1;
          }
          .stButton > button:disabled * { color:var(--faint) !important; }

          /* ---- Inputs / selects / sliders ---- */
          div[data-baseweb="select"] > div, div[data-baseweb="input"] > div {
            min-height:46px; border-radius:12px; border-color:var(--line-2);
            background:var(--panel); box-shadow:var(--shadow-sm); transition:all .18s var(--ease);
          }
          div[data-baseweb="select"] > div:hover { border-color:#c8cde0; }
          div[data-baseweb="select"] > div:focus-within { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }
          .stMultiSelect [data-baseweb="tag"] { background:var(--accent); color:#fff; border-radius:8px; }
          .stSlider label, .stMultiSelect label, .stSelectbox label, .stRadio label {
            font-weight:600; color:var(--ink-2); font-size:.9rem;
          }
          div[data-testid="stSlider"] div[role="slider"] { background:var(--accent) !important; border-color:var(--accent) !important; }
          div[data-testid="stSlider"] [data-baseweb="slider"] [data-testid="stThumbValue"] { color:var(--accent); font-weight:600; }

          /* radios as soft chips */
          div[role="radiogroup"] label {
            background:var(--panel); border:1px solid var(--line-2); border-radius:12px;
            padding:10px 14px; margin-bottom:8px; box-shadow:var(--shadow-sm); transition:all .15s var(--ease);
          }
          div[role="radiogroup"] label:hover { border-color:var(--accent); }
          div[role="radiogroup"] label:has(input:checked) {
            border-color:var(--accent); background:var(--accent-soft); box-shadow:var(--shadow-md);
          }
          div[role="radiogroup"] label:has(input:checked) p { color:var(--ink); font-weight:600; }

          /* ---- Containers / dataframes / expanders ---- */
          div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius:var(--radius); border-color:var(--line-2); box-shadow:var(--shadow-sm);
          }
          [data-testid="stDataFrame"] { border:1px solid var(--line-2); border-radius:14px; overflow:hidden;
            box-shadow:var(--shadow-sm); }
          div[data-testid="stExpander"] { border:1px solid var(--line-2); border-radius:14px;
            box-shadow:var(--shadow-sm); }
          div[data-testid="stNotification"] { border-radius:14px; box-shadow:var(--shadow-sm); }
          [data-testid="stStatusWidget"], div[data-testid="stStatus"] { border-radius:14px; }

          /* ---- Remediation terminal ---- */
          .fg-term {
            background:#0b0f17; color:#cdd6e3; border:1px solid #1b2636; border-radius:14px;
            padding:16px 18px; font-size:12.5px; line-height:1.6; white-space:pre-wrap; word-break:break-word;
            font-family:'JetBrains Mono','SFMono-Regular',Menlo,Consolas,'Liberation Mono',monospace;
            box-shadow:inset 0 0 0 1px rgba(255,255,255,.02), var(--shadow-sm); overflow-x:auto; max-height:460px;
          }
          .fg-term .c-prompt { color:#5ec8ff; font-weight:700; }
          .fg-term .c-cmd    { color:#eef3fb; font-weight:600; }
          .fg-term .c-out    { color:#8a9bb2; }
          .fg-term .c-ok     { color:#49d17a; font-weight:600; }
          .fg-term .c-warn   { color:#ffce5a; }
          .fg-term .c-dim    { color:#5c6b82; }
          .fg-term .c-cursor { color:#5ec8ff; animation: fgblink 1s steps(2) infinite; }
          @keyframes fgblink { 50% { opacity:0; } }
          .fg-termbar {
            display:flex; align-items:center; gap:7px; padding:9px 14px; background:#121824;
            border:1px solid #1b2636; border-bottom:none; border-radius:12px 12px 0 0;
            font-family:'JetBrains Mono',monospace; font-size:11.5px; color:#7f8ea3;
          }
          .fg-termbar .dot { width:11px; height:11px; border-radius:50%; display:inline-block; }
          .fg-termbar .d1 { background:#ff5f56; } .fg-termbar .d2 { background:#ffbd2e; } .fg-termbar .d3 { background:#27c93f; }
          .fg-termbar + .fg-term { border-radius:0 0 14px 14px; }

          /* ---- Scrollbars ---- */
          ::-webkit-scrollbar { width:10px; height:10px; }
          ::-webkit-scrollbar-thumb { background:#d2d2d8; border-radius:999px; border:2px solid var(--bg); }
          ::-webkit-scrollbar-thumb:hover { background:#b9b9c1; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def brand_header(jev) -> None:  # noqa: ANN001
    live = getattr(jev, "live", False)
    status = (
        f"<div class='fg-status live'><span class='fg-dot'></span>Jev LIVE · {jev.model}</div>"
        if live else ""
    )
    st.markdown(
        "<div class='fg-topbar'>"
        "  <div class='fg-brand'>"
        "    <div class='fg-logo'>🛡️</div>"
        "    <div>"
        "      <div class='fg-title'>Fin<span>Guard</span>.ai</div>"
        "      <div class='fg-sub'>Jev-Powered Multi-Cloud FinOps Micro-Governor</div>"
        "    </div>"
        "  </div>"
        f"  {status}"
        "</div>",
        unsafe_allow_html=True,
    )


def page_header(title: str, subtitle: str, pill: Optional[str] = None) -> None:
    pill_html = f"<span class='fg-pill'>{pill}</span><br>" if pill else ""
    st.markdown(
        f"<div class='fg-banner'>{pill_html}<h1>{title}</h1><p>{subtitle}</p></div>",
        unsafe_allow_html=True,
    )


def band_badge(band: ConfidenceBand) -> str:
    fg, bg, label = BAND_STYLE[band]
    return (
        f"<span style='background:{bg};color:{fg};padding:3px 10px;border-radius:12px;"
        f"font-weight:700;font-size:0.76rem'>{label}</span>"
    )


def band_from(conf: float, high: float, mid: float) -> ConfidenceBand:
    if conf >= high:
        return ConfidenceBand.HIGH
    if conf >= mid:
        return ConfidenceBand.MID
    return ConfidenceBand.LOW


def client_badge(jev) -> str:  # noqa: ANN001
    if getattr(jev, "live", False):
        return (
            "<span class='fg-pill' style='background:#0d2f6b;color:#9cc2ff'>"
            f"LIVE · {jev.model}</span>"
        )
    return (
        "<span class='fg-pill' style='background:#2a2f3a;color:#c7ccd6'>"
        "DEMO HEURISTIC · add OPENROUTER_API_KEY for live Jev</span>"
    )


def render_latency(resp: JevResponse) -> None:
    c1, c2, c3 = st.columns(3)
    c1.metric("Jev latency", f"{resp.latency_ms:.0f} ms")
    c2.metric("Overall confidence", f"{resp.overall_confidence()*100:.0f}%")
    c3.metric("Decision lane", BAND_STYLE[resp.overall_band()][2].split(" ")[0])
    if resp.error:
        st.caption(f":orange[Fell back to demo heuristic — {resp.error}]")


def render_answer_row(name: str, resp: JevResponse) -> None:
    val = resp.answers.get(name)
    conf = resp.confidences.get(name, 0.0)
    band = resp.band(name)
    st.markdown(
        f"**`{name}`** → **{val}**  &nbsp; {band_badge(band)} "
        f"&nbsp; <span style='color:#8a93a3'>conf {conf*100:.0f}%</span>",
        unsafe_allow_html=True,
    )
    rat = resp.rationales.get(name)
    if rat:
        st.caption(rat)
