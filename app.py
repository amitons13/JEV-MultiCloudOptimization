"""FinGuard.ai — The Jev-Powered Multi-Cloud FinOps Micro-Governor.

Three tabs:
  1. Log Explorer — self-serve analytics: slice the logs, analyze the metrics,
     and drill into any single log's problem areas.
  2. FinOps Control Center — Jev triages the multi-cloud logs and drives
     per-resource remediation workflows (Advisory mode).
  3. Benefits — realistic, data-driven financial impact (cost saved, ROI, speed).
"""

from __future__ import annotations

import streamlit as st
from dotenv import load_dotenv

from jev import JevClient
from modules import benefits, explorer, log_triage
from modules.ui import brand_header, inject_css

load_dotenv()

st.set_page_config(
    page_title="FinGuard.ai · Jev Multi-Cloud FinOps",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def get_jev() -> JevClient:
    return JevClient()


def main() -> None:
    inject_css()
    jev = get_jev()
    brand_header(jev)

    tab_explorer, tab_finops, tab_benefits = st.tabs(
        ["① Log Explorer", "② FinOps Control Center", "③ Benefits"]
    )
    with tab_explorer:
        explorer.render()
    with tab_finops:
        log_triage.render(jev)
    with tab_benefits:
        benefits.render()


if __name__ == "__main__":
    main()
