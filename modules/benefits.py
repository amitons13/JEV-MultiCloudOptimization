"""Benefits tab — realistic, data-driven financial impact of FinGuard.ai.

All numbers are derived directly from the Azure / AWS / GCP resources in logs/:
projected monthly waste per resource, recovered by a category-specific capture
rate, net of Jev's (tiny) operating cost.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .data import (
    AVG_TOKENS_PER_DECISION,
    CAPTURE_BY_CATEGORY,
    JEV_PRICE_PER_MTOK_INPUT,
    benefits_breakdown,
)
from .ui import CLOUD_COLORS, page_header, style_fig


def render() -> None:
    page_header(
        "Benefits — realized FinOps impact",
        "What FinGuard.ai is worth on this estate: cost saved, ROI and speed — computed "
        "directly from the Azure, AWS and GCP resources in the logs.",
        pill="BUSINESS VALUE · FROM REAL CLOUD DATA",
    )

    b = benefits_breakdown()

    # ---- headline KPIs ----
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Monthly waste identified", f"${b['total_waste']:,.0f}")
    k2.metric("Monthly savings", f"${b['recoverable']:,.0f}", f"{b['pct_recoverable']:.0f}% recoverable")
    k3.metric("Annual savings", f"${b['recoverable']*12:,.0f}")
    k4.metric("Jev cost / ROI", f"${b['jev_cost']:,.0f}/mo", f"{b['roi']:,.0f}x return")

    # ---- waterfall: waste -> savings -> net ----
    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "relative", "total"],
        x=["Waste identified", "Not recovered", "Jev operating cost", "Net monthly savings"],
        y=[b["total_waste"], -(b["total_waste"] - b["recoverable"]), -b["jev_cost"], b["net"]],
        text=[f"${b['total_waste']:,.0f}", f"-${b['total_waste']-b['recoverable']:,.0f}",
              f"-${b['jev_cost']:,.0f}", f"${b['net']:,.0f}"],
        textposition="outside",
        connector={"line": {"color": "#c9cdd6"}},
        decreasing={"marker": {"color": "#d98a9e"}},
        increasing={"marker": {"color": "#3fa796"}},
        totals={"marker": {"color": "#4f5bd5"}},
    ))
    st.plotly_chart(style_fig(fig, "Monthly waste → net savings", 420), use_container_width=True)

    # ---- per-cloud + per-category ----
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("##### Savings by cloud")
        clouds = sorted(b["by_cloud_save"], key=lambda k: b["by_cloud_save"][k], reverse=True)
        fig = go.Figure(go.Bar(
            x=clouds, y=[b["by_cloud_save"][k] for k in clouds],
            marker_color=[CLOUD_COLORS.get(k, "#888") for k in clouds],
            text=[f"${b['by_cloud_save'][k]:,.0f}" for k in clouds], textposition="outside",
        ))
        fig.update_layout(yaxis_title="$/mo saved")
        st.plotly_chart(style_fig(fig, height=320), use_container_width=True)
    with c2:
        st.markdown("##### Savings by waste category")
        cats = dict(sorted(b["by_cat_save"].items(), key=lambda kv: kv[1], reverse=True))
        fig = go.Figure(go.Bar(
            x=list(cats.values()), y=list(cats.keys()), orientation="h", marker_color="#4f5bd5",
        ))
        fig.update_layout(xaxis_title="$/mo saved")
        st.plotly_chart(style_fig(fig, height=320), use_container_width=True)

    # ---- per-cloud detail table ----
    st.markdown("##### Per-cloud breakdown")
    rows = []
    for cloud in clouds:
        waste = b["by_cloud_waste"][cloud]
        save = b["by_cloud_save"][cloud]
        rows.append({
            "cloud": cloud,
            "monthly_waste_$": round(waste),
            "monthly_savings_$": round(save),
            "annual_savings_$": round(save * 12),
            "recovered_%": f"{(save/waste*100 if waste else 0):.0f}%",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # ---- why Jev: efficiency of the decision layer ----
    st.divider()
    st.markdown("##### Why Jev as the decision layer")
    e1, e2, e3 = st.columns(3)
    e1.metric("Jev decisions / month", f"{b['jev_decisions']:,}")
    e2.metric("Cost per decision", f"${AVG_TOKENS_PER_DECISION*JEV_PRICE_PER_MTOK_INPUT/1_000_000:.6f}")
    e3.metric("Savings per $1 of Jev", f"${b['roi']:,.0f}")
    st.caption(
        f"Jev scans the estate every 15 minutes (~{b['jev_decisions']:,} decisions/mo) at "
        f"${JEV_PRICE_PER_MTOK_INPUT}/1M input tokens with free output — a **${b['jev_cost']:,.0f}/mo** "
        f"decision layer that recovers **${b['recoverable']:,.0f}/mo**. A frontier LLM in the same "
        "inline role would cost orders of magnitude more and add seconds of latency per decision."
    )

    with st.expander("How these numbers are computed"):
        st.markdown(
            "- **Monthly waste** = sum of `projectedMonthlyWasteUSD` across every resource in `logs/`.\n"
            "- **Savings** = waste × a realistic capture rate per waste category:"
        )
        st.dataframe(
            pd.DataFrame([{"waste_category": k, "capture_rate": f"{v*100:.0f}%"} for k, v in CAPTURE_BY_CATEGORY.items()]),
            use_container_width=True, hide_index=True,
        )
        st.markdown(
            f"- **Jev cost** = decisions × {AVG_TOKENS_PER_DECISION} input tokens × "
            f"${JEV_PRICE_PER_MTOK_INPUT}/1M (output free).\n"
            "- **Net savings** = savings − Jev cost. **ROI** = savings ÷ Jev cost."
        )
