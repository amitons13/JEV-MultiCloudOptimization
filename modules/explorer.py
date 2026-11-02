"""Log Explorer — select multi-cloud logs, slice them, and analyze the metrics.

A self-serve analytics view over the same normalized Azure / AWS / GCP logs that
Jev governs. Presets default to the full estate; narrow by cloud, waste category,
environment or service, then drill into any single resource's raw log.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from .data import CAPTURE_BY_CATEGORY, all_normalized
from .ui import CLOUD_COLORS, GRAY_SEQ, page_header, style_fig

# What each waste category means + the metric that proves it.
CATEGORY_PROBLEM: Dict[str, tuple] = {
    "Idle": ("Provisioned but barely used", "Utilization is far below a healthy floor — you pay 24/7 for near-zero work."),
    "Oversized": ("Capacity far exceeds real demand", "Sustained low utilization on a large SKU — right-sizing recovers most of it."),
    "Orphaned": ("Unattached resource with no owner workload", "Still billing with zero consumers — safe to delete."),
    "UnencryptedIdle": ("Idle AND non-compliant (no encryption at rest)", "Combines wasted spend with a security/compliance exposure."),
    "RunawayLoop": ("Runaway / recursive execution burning spend", "Invocation or throughput spike with no business value — stop the loop now."),
    "ColdStorage": ("Rarely-accessed data on a hot/expensive tier", "Old objects never transitioned to an archive tier."),
    "InefficientRouting": ("Traffic sent to an over-powered, expensive tier", "Simple requests hitting premium models/instances."),
    "ObservabilityWaste": ("Excessive logging / metrics retention", "Verbose telemetry kept far longer than anyone queries it."),
    "Other": ("Spend anomaly flagged for review", "Cost pattern deviates from the expected baseline."),
}

# Raw-log key fragments that typically point at the problem.
PROBLEM_KEYWORDS = (
    "util", "waste", "cost", "recursive", "idle", "invocation", "encrypt", "retention",
    "connection", "query", "orphan", "unattached", "available", "throttle", "concurrency",
    "gpu", "access", "lastused", "last_used", "uptime", "requests", "egress", "ingest",
)


def _est_savings(r: Dict[str, Any]) -> float:
    return r["monthly_waste_usd"] * CAPTURE_BY_CATEGORY.get(r["waste_category"], 0.5)


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}{i}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _problem_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Surface the raw-log keys that actually evidence the waste."""
    flat = _flatten(raw)
    return {k: v for k, v in flat.items() if any(w in k.lower() for w in PROBLEM_KEYWORDS)}


def render() -> None:
    page_header(
        "Log Explorer",
        "Slice the ingested multi-cloud logs any way you like, watch the metrics "
        "update live, then drill into a single resource's raw export.",
        pill="SELF-SERVE ANALYTICS",
    )

    rows = all_normalized()
    clouds_all = sorted({r["cloud"] for r in rows})
    cats_all = sorted({r["waste_category"] for r in rows})
    envs_all = sorted({r["environment"] for r in rows})
    svcs_all = sorted({r["category"] for r in rows})

    # ---- Filters (presets default to the full estate) ------------------- #
    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)
    clouds = c1.multiselect("Cloud", clouds_all, default=clouds_all)
    cats = c2.multiselect("Waste category", cats_all, default=cats_all)
    envs = c3.multiselect("Environment", envs_all, default=envs_all)
    svcs = c4.multiselect("Service", svcs_all, default=svcs_all)

    sel = [
        r for r in rows
        if r["cloud"] in clouds and r["waste_category"] in cats
        and r["environment"] in envs and r["category"] in svcs
    ]

    if not sel:
        st.warning("No logs match the current filters — widen your selection.")
        return

    # ---- KPIs ----------------------------------------------------------- #
    total_waste = sum(r["monthly_waste_usd"] for r in sel)
    recoverable = sum(_est_savings(r) for r in sel)
    avg_util = sum(r["utilization_pct"] for r in sel) / len(sel)
    protected = sum(1 for r in sel if r["environment"] in {"prod", "dr"})

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Logs selected", f"{len(sel):,}", f"of {len(rows):,}")
    k2.metric("Projected waste", f"${total_waste:,.0f}/mo")
    k3.metric("Recoverable", f"${recoverable:,.0f}/mo",
              f"{(recoverable/total_waste*100 if total_waste else 0):.0f}%")
    k4.metric("Avg utilization", f"{avg_util:.0f}%")
    k5.metric("Protected (prod/dr)", f"{protected}")

    _charts(sel)

    # ---- Table (click a row to expand its details) --------------------- #
    st.markdown("#### Selected logs")
    st.caption("Click any row to expand its full details and see exactly which parts of the log are the problem.")
    sorted_sel = sorted(sel, key=lambda x: x["monthly_waste_usd"], reverse=True)
    df = pd.DataFrame([
        {
            "cloud": r["cloud"],
            "resource": r["resource"],
            "service": r["category"],
            "waste_category": r["waste_category"],
            "env": r["environment"],
            "team": r["team"],
            "region": r["region"],
            "sku": r["sku"],
            "util_%": round(r["utilization_pct"], 0),
            "waste_$mo": round(r["monthly_waste_usd"], 0),
            "recoverable_$mo": round(_est_savings(r), 0),
        }
        for r in sorted_sel
    ])
    event = st.dataframe(
        df, use_container_width=True, height=340, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="fg_explorer_table",
        column_config={
            "waste_$mo": st.column_config.NumberColumn("Waste", format="$%d/mo"),
            "recoverable_$mo": st.column_config.NumberColumn("Recoverable", format="$%d/mo"),
            "util_%": st.column_config.ProgressColumn("Util", format="%d%%", min_value=0, max_value=100),
        },
    )
    st.download_button("Download selection (CSV)", df.to_csv(index=False).encode("utf-8"),
                       file_name="finguard_log_selection.csv", mime="text/csv")

    sel_rows = list(event.selection.rows) if event and event.selection else []
    if sel_rows:
        _detail(sorted_sel[sel_rows[0]])
    else:
        st.info("⬆️ Select a row above to expand the log and highlight its problem areas.")


def _detail(r: Dict[str, Any]) -> None:
    headline, why = CATEGORY_PROBLEM.get(r["waste_category"], CATEGORY_PROBLEM["Other"])
    capture = CAPTURE_BY_CATEGORY.get(r["waste_category"], 0.5)

    with st.container(border=True):
        st.markdown(f"### 🔍 {r['resource']} — {r['cloud']} · {r['category']}")
        st.markdown(f"**Problem — {r['waste_category']}:** {headline}. {why}")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Utilization", f"{r['utilization_pct']:.0f}%",
                  "under-utilized" if r["utilization_pct"] < 30 else None,
                  delta_color="inverse")
        m2.metric("Projected waste", f"${r['monthly_waste_usd']:,.0f}/mo")
        m3.metric("Recoverable", f"${_est_savings(r):,.0f}/mo", f"{capture*100:.0f}% capture")
        m4.metric("Environment", r["environment"],
                  "protected" if r["environment"] in {"prod", "dr"} else None, delta_color="off")

        c1, c2 = st.columns([1, 1])
        with c1:
            st.markdown("**Resource details**")
            st.markdown(
                f"- **Owner team:** `{r['team']}`\n"
                f"- **Region:** {r['region']}\n"
                f"- **SKU / type:** `{r['sku']}` · {r['resource_type']}\n"
                f"- **Recommended action:** {r['recommended_action'] or '—'}"
            )
            st.markdown("**⚠️ Problem areas in the log**")
            problem = _problem_fields(r["raw"])
            if problem:
                pdf = pd.DataFrame(
                    [{"field": k, "value": str(v)} for k, v in problem.items()]
                )
                st.dataframe(
                    pdf, use_container_width=True, hide_index=True, height=min(260, 40 + 35 * len(pdf)),
                    column_config={
                        "field": st.column_config.TextColumn("Log field", width="medium"),
                        "value": st.column_config.TextColumn("Value", width="medium"),
                    },
                )
                st.caption("These are the exact log fields that evidence the waste — the signals Jev reads to decide.")
            else:
                st.caption("No specific sub-fields flagged; the waste is inferred from the resource-level metrics above.")
        with c2:
            st.markdown("**Full raw provider log**")
            st.json(r["raw"], expanded=True)


def _charts(sel: List[Dict[str, Any]]) -> None:
    by_svc: Dict[str, float] = {}
    by_cat: Dict[str, float] = {}
    by_env: Dict[str, float] = {}
    for r in sel:
        by_svc[r["category"]] = by_svc.get(r["category"], 0.0) + r["monthly_waste_usd"]
        by_cat[r["waste_category"]] = by_cat.get(r["waste_category"], 0.0) + r["monthly_waste_usd"]
        by_env[r["environment"]] = by_env.get(r["environment"], 0.0) + r["monthly_waste_usd"]

    c1, c2, c3 = st.columns(3)
    with c1:
        d = dict(sorted(by_svc.items(), key=lambda kv: kv[1], reverse=True))
        fig = go.Figure(go.Bar(x=list(d.values()), y=list(d.keys()), orientation="h", marker_color="#4f5bd5"))
        st.plotly_chart(style_fig(fig, "Waste by service ($/mo)", 300), use_container_width=True)
    with c2:
        d = dict(sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True))
        fig = go.Figure(go.Bar(x=list(d.keys()), y=list(d.values()), marker_color="#3fa796",
                               text=[f"${v:,.0f}" for v in d.values()], textposition="outside"))
        fig.update_layout(xaxis_tickangle=-30)
        st.plotly_chart(style_fig(fig, "Waste by category ($/mo)", 300), use_container_width=True)
    with c3:
        fig = go.Figure(go.Pie(labels=list(by_env.keys()), values=list(by_env.values()), hole=0.5,
                               marker_colors=GRAY_SEQ))
        st.plotly_chart(style_fig(fig, "Waste by environment", 300), use_container_width=True)

    # utilization vs waste scatter (bubble = recoverable)
    df = pd.DataFrame([
        {
            "utilization": r["utilization_pct"],
            "waste": r["monthly_waste_usd"],
            "recoverable": _est_savings(r),
            "cloud": r["cloud"],
            "resource": r["resource"],
            "category": r["waste_category"],
        }
        for r in sel
    ])
    fig = px.scatter(
        df, x="utilization", y="waste", size="recoverable", color="cloud",
        hover_name="resource", hover_data=["category"],
        color_discrete_map=CLOUD_COLORS, size_max=38,
        labels={"utilization": "Utilization (%)", "waste": "Projected waste ($/mo)"},
    )
    st.plotly_chart(style_fig(fig, "Utilization vs. waste (bubble = recoverable $)", 380),
                    use_container_width=True)
