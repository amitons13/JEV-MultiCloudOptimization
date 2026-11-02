"""FinOps Control Center (Module 2) — streamlined Jev triage + recommendations.

Load multi-cloud logs → set a few policy knobs → Jev triages every resource in one
pass (Score + Choice + Noul) → prioritized, approvable remediation queue.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from jev import ConfidenceBand, JevClient, Question, QType
from .data import CAPTURE_BY_CATEGORY, all_normalized
from .ui import band_from, page_header

REMEDIATION_TARGETS = [
    "AWS_Lambda_Kill", "AWS_EC2_Stop", "AWS_RDS_RightSize",
    "GCP_CloudRun_ScaleDown", "GCP_GKE_ScaleToZero",
    "Azure_VM_Resize", "Azure_Pause_Pool",
    "Delete_Orphaned_Resource", "Storage_Lifecycle_Tier", "Enable_Encryption", "No_Action",
]

SEVERITY_LABELS = {
    1: "negligible background noise",
    2: "minor",
    3: "notable recurring waste",
    4: "serious idle / oversized spend",
    5: "massive runaway loop or unencrypted idle DB",
}

LANE_AUTO = "Auto-remediate"
LANE_ESC = "Escalate · human review"
LANE_DEEP = "Deep reasoning LLM"
LANE_NONE = "Below threshold"

LANE_EMOJI = {LANE_AUTO: "🟢 Auto-remediate", LANE_ESC: "🟡 Escalate", LANE_DEEP: "🟠 Deep reasoning", LANE_NONE: "⚪ Below threshold"}


def _lane_display(lane: str) -> str:
    return LANE_EMOJI.get(lane, lane)

PROTECTED_ENVS = {"prod", "dr"}


def _questions() -> List[Question]:
    return [
        Question("waste_severity", QType.SCORE, "Rate the financial waste severity of this resource.",
                 scale=(1, 5), scale_labels=SEVERITY_LABELS),
        Question("remediation_target", QType.CHOICE,
                 "Pick the single best automated remediation action.", options=REMEDIATION_TARGETS),
        Question("is_urgent_action_required", QType.NOUL,
                 "Does this need urgent (within-the-hour) action to stop active cost bleed?"),
    ]


def _context_for(r: Dict[str, Any]) -> str:
    return (
        f"Cloud={r['cloud']} Service={r['category']} Resource={r['resource']} "
        f"Type={r['resource_type']} Region={r['region']} SKU={r['sku']} Env={r['environment']} "
        f"Utilization={r['utilization_pct']}% WasteCategory={r['waste_category']} "
        f"ProjectedMonthlyWasteUSD={r['monthly_waste_usd']} Hint={r['recommended_action']}\n"
        f"RawLog={json.dumps(r['raw'])[:1000]}"
    )


def render(jev: JevClient) -> None:
    page_header(
        "FinOps Control Center",
        "Jev triages every multi-cloud resource in a single split-second pass, then the "
        "policy decides each lane: auto-remediate, escalate to a human, or deep-reason.",
    )

    rows = all_normalized()
    clouds_all = sorted({r["cloud"] for r in rows})

    with st.container():
        c1, c2, c3 = st.columns([1.8, 1, 1])
        clouds = c1.multiselect("Clouds", clouds_all, default=clouds_all)
        high = c2.slider("Auto ≥ confidence", 0.6, 0.99, 0.85, 0.01,
                         help="Resources Jev is at least this confident about are safe to auto-remediate.")
        min_sev = c3.slider("Min severity", 1, 5, 2)

    st.caption("**Mode: Advisory** — Jev recommends; nothing is applied until an operator launches a workflow.")

    scoped = [r for r in rows if r["cloud"] in clouds]
    engine = getattr(jev, "engine_label", "Live Jev" if getattr(jev, "live", False) else "Simulation")
    st.caption(
        f"{len(scoped)} resources in scope · ${sum(r['monthly_waste_usd'] for r in scoped):,.0f}/mo "
        f"projected waste · protected envs: {', '.join(sorted(PROTECTED_ENVS))} · "
        f"engine: **{engine}** _(set `JEV_MODE` in config)_"
    )

    if st.button("Run Jev triage", type="primary"):
        if not scoped:
            st.error("Select at least one cloud.")
            return
        settings = {"high": high, "mid": max(0.5, high - 0.2), "min_sev": min_sev}
        results: List[Dict[str, Any]] = []
        qs = _questions()
        total_ms = 0.0
        with st.status(f"🛰️ Jev is triaging {len(scoped)} multi-cloud resources…", expanded=True) as status:
            prog = st.progress(0.0)
            step = max(1, len(scoped) // 12)
            for i, r in enumerate(scoped, start=1):
                resp = jev.ask(_context_for(r), qs)
                total_ms += resp.latency_ms
                results.append(_govern(r, resp, settings))
                prog.progress(i / len(scoped))
                if i % step == 0 or i == len(scoped):
                    status.write(f"✓ {i}/{len(scoped)} scored · latest `{r['resource']}` ({resp.latency_ms:.0f} ms)")
            status.update(
                label=f"✅ Triage complete — {len(scoped)} resources in {total_ms:,.0f} ms total Jev compute",
                state="complete", expanded=False,
            )
        st.session_state["fg_results"] = results

    if st.session_state.get("fg_results"):
        _recommendations(st.session_state["fg_results"])


def _govern(r: Dict[str, Any], resp, s: Dict[str, Any]) -> Dict[str, Any]:
    conf = resp.overall_confidence()
    band = band_from(conf, s["high"], s["mid"])
    severity = resp.answers.get("waste_severity") or 1
    target = resp.answers.get("remediation_target") or "No_Action"

    if severity < s["min_sev"] or target == "No_Action":
        why = f"severity {severity} < min {s['min_sev']}" if severity < s["min_sev"] else "Jev chose No_Action"
        lane, reason = LANE_NONE, why
    elif band is ConfidenceBand.HIGH:
        if r["environment"] in PROTECTED_ENVS:
            lane, reason = LANE_ESC, f"high confidence, but protected env '{r['environment']}' needs sign-off"
        else:
            lane, reason = LANE_AUTO, f"high confidence ({conf:.0%}) — safe to auto-remediate"
    elif band is ConfidenceBand.MID:
        lane, reason = LANE_ESC, f"mid confidence ({conf:.0%}) — human review"
    else:
        lane, reason = LANE_DEEP, f"low confidence ({conf:.0%}) — route to deep reasoning LLM"

    # Realistic recoverable fraction depends on the waste type, not the lane.
    capture = 0.0 if lane == LANE_NONE else CAPTURE_BY_CATEGORY.get(r["waste_category"], 0.5)
    return {
        "cloud": r["cloud"], "resource": r["resource"], "service": r["category"],
        "resource_type": r["resource_type"], "region": r["region"], "sku": r["sku"],
        "utilization_pct": r["utilization_pct"],
        "waste_category": r["waste_category"], "environment": r["environment"],
        "severity": severity, "target": target, "confidence": conf, "lane": lane, "reason": reason,
        "waste": r["monthly_waste_usd"], "est_savings": r["monthly_waste_usd"] * capture,
        "latency": resp.latency_ms, "recommended_action": r["recommended_action"],
        "raw": r["raw"],
    }


WHY_BY_CATEGORY = {
    "Idle": "Running 24/7 at near-zero utilization",
    "Oversized": "SKU far larger than sustained demand",
    "Orphaned": "Unattached — still billing with no consumers",
    "UnencryptedIdle": "Idle and unencrypted (cost + compliance risk)",
    "RunawayLoop": "Runaway / recursive execution burning spend",
    "ColdStorage": "Cold data sitting on a hot, expensive tier",
    "InefficientRouting": "Premium tier used for simple traffic",
    "ObservabilityWaste": "Verbose logs kept far beyond any use",
    "Other": "Spend pattern deviates from baseline",
}


def _why(x: Dict[str, Any]) -> str:
    base = WHY_BY_CATEGORY.get(x["waste_category"], "Flagged for review")
    return f"{base} — {x['utilization_pct']:.0f}% util, {x['confidence']:.0%} Jev confidence"


def _recommendations(res: List[Dict[str, Any]]) -> None:
    res_sorted = sorted(res, key=lambda x: x["est_savings"], reverse=True)

    st.markdown("#### Jev recommendations")
    st.caption("Each row is a Jev recommendation with **why** it was flagged. Click a row to Remediate or Explain it.")
    df = pd.DataFrame([
        {
            "#": i + 1,
            "cloud": x["cloud"],
            "resource": x["resource"],
            "recommendation": _one_liner(x),
            "why": _why(x),
            "save_$mo": round(x["est_savings"], 0),
            "lane": _lane_display(x["lane"]),
            "status": _status_label(x),
        }
        for i, x in enumerate(res_sorted)
    ])
    event = st.dataframe(
        df, use_container_width=True, height=360, hide_index=True,
        on_select="rerun", selection_mode="single-row", key="fg_reco_table",
        column_config={
            "recommendation": st.column_config.TextColumn("Recommended action", width="large"),
            "why": st.column_config.TextColumn("Why it's a recommendation", width="large"),
            "save_$mo": st.column_config.NumberColumn("Savings", format="$%d/mo"),
            "lane": st.column_config.TextColumn("Decision lane", width="medium"),
            "#": st.column_config.NumberColumn("#", width="small"),
        },
    )
    st.download_button("Download recommendations (CSV)", df.to_csv(index=False).encode("utf-8"),
                       file_name="finguard_recommendations.csv", mime="text/csv")

    sel_rows = list(event.selection.rows) if event and event.selection else []
    if not sel_rows:
        st.info("⬆️ Select a recommendation row to open its **Remediate** and **Explain** tabs.")
        return

    x = res_sorted[sel_rows[0]]
    st.markdown(f"#### {x['resource']} — {x['cloud']} · {x['service']}")
    tab_rem, tab_exp = st.tabs(["🔧 Remediate", "📖 Explain"])
    with tab_rem:
        _remediate_tab(x)
    with tab_exp:
        _explain_tab(x)


def _remediate_tab(x: Dict[str, Any]) -> None:
    plan = _plan(x)
    eta = sum(_step_seconds(s["title"]) for s in plan)
    c1, c2, c3 = st.columns(3)
    c1.metric("Action", x["target"])
    c2.metric("Projected savings", f"${x['est_savings']:,.0f}/mo")
    c3.metric("Est. runtime", _fmt(eta))
    st.markdown(f"**Recommendation:** {_one_liner(x)}")
    st.markdown("**Remediation steps**")
    for i, s in enumerate(plan, 1):
        st.markdown(f"**{i}. {s['title']}** — {s['detail']}  ·  _~{_fmt(_step_seconds(s['title']))}_")
    if st.button("▶ Open remediation workflow", type="primary", key=f"open_{x['cloud']}_{x['resource']}"):
        _remediation_dialog(x)


def _explain_tab(x: Dict[str, Any]) -> None:
    c1, c2 = st.columns([1, 1])
    with c1:
        st.markdown("**Why Jev flagged this**")
        st.markdown(_explanation(x))
        st.markdown(f"> {_why(x)}")
    with c2:
        st.markdown("**Originating log (source of the problem)**")
        st.json(x["raw"], expanded=False)


# --------------------------------------------------------------------------- #
# Per-recommendation drilldown + remediation workflow (modal)
# --------------------------------------------------------------------------- #
def _resource_ref(x: Dict[str, Any]) -> str:
    """Best available provider resource identifier for CLI commands."""
    raw = x.get("raw", {}) or {}
    ref = raw.get("lineItem/ResourceId") or raw.get("ResourceId")
    if ref:
        return ref
    res = raw.get("resource", {}) or {}
    labels = res.get("labels", {}) or {}
    if labels:
        proj = labels.get("project_id", "project")
        loc = labels.get("zone") or labels.get("region") or labels.get("location") or "global"
        return f"projects/{proj}/locations/{loc}/{x['resource']}"
    return x["resource"]


def _one_liner(x: Dict[str, Any]) -> str:
    """Human, specific, one-line recommendation for the queue table."""
    fix = (x.get("recommended_action") or "").strip().rstrip(".")
    sku = x.get("sku") or ""
    util = x.get("utilization_pct", 0.0)
    if fix:
        # already specific in the data (e.g. "Right-size D64s_v5 -> D8s_v5"); enrich lightly
        if sku and sku.lower() not in fix.lower() and len(fix) < 70:
            return f"{fix} ({sku}, ~{util:.0f}% util)"
        return fix
    verbs = {
        "Idle": f"Stop idle {sku} ({util:.0f}% utilization)",
        "Oversized": f"Right-size over-provisioned {sku} ({util:.0f}% utilization)",
        "Orphaned": f"Delete orphaned {x['resource_type']}",
        "RunawayLoop": "Kill runaway loop and cap concurrency",
        "ColdStorage": "Move cold data to an archive storage tier",
        "UnencryptedIdle": f"Right-size and enable encryption on {sku}",
        "InefficientRouting": "Route simple requests to a cheaper model tier",
        "ObservabilityWaste": "Drop verbose logs and shorten retention",
    }
    return verbs.get(x["waste_category"], f"Remediate {x['waste_category']} on {x['resource']}")


# Rich, resource-aware remediation plans: each step = (title, detail, command).
def _plan(x: Dict[str, Any]) -> List[Dict[str, str]]:
    ref, region, res, sku = _resource_ref(x), (x.get("region") or "us-east-1"), x["resource"], (x.get("sku") or "target-sku")
    tgt, cloud = x["target"], x["cloud"]

    def step(title: str, detail: str, cmd: str) -> Dict[str, str]:
        return {"title": title, "detail": detail, "cmd": cmd}

    if tgt == "AWS_EC2_Stop":
        return [
            step("Pre-flight checks", "Confirm no active sessions, ASG membership or in-flight jobs before stopping.",
                 f"aws ec2 describe-instances --instance-ids {ref} --query 'Reservations[].Instances[].State.Name'"),
            step("Create restore point", "Take an AMI so the box can be recreated identically if needed.",
                 f"aws ec2 create-image --instance-id {ref} --name '{res}-prestop' --no-reboot"),
            step("Stop the instance", "Deallocate compute; EBS storage is retained.",
                 f"aws ec2 stop-instances --instance-ids {ref} --region {region}"),
            step("Verify stopped state", "Block until the instance reports 'stopped'.",
                 f"aws ec2 wait instance-stopped --instance-ids {ref} --region {region}"),
            step("Tag & record savings", "Annotate for the auto-start schedule and log the saving.",
                 f"aws ec2 create-tags --resources {ref} --tags Key=finguard:action,Value=stopped"),
        ]
    if tgt == "AWS_Lambda_Kill":
        return [
            step("Throttle the function", "Set reserved concurrency to 0 to immediately halt the storm.",
                 f"aws lambda put-function-concurrency --function-name {res} --reserved-concurrent-executions 0"),
            step("Disable the trigger", "Detach the event source so it cannot re-fire.",
                 f"aws lambda list-event-source-mappings --function-name {res}"),
            step("Drain in-flight invocations", "Wait for running executions to complete and error rate to fall.",
                 f"aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value={res}"),
            step("Confirm zero traffic", "Verify invocation rate is 0 before closing.",
                 "watch 'aws lambda get-function --function-name %s'" % res),
            step("Open RCA ticket", "File the root-cause for the recursion / retry loop.",
                 "jira create --type Incident --summary 'Recursive Lambda storm: %s'" % res),
        ]
    if tgt == "AWS_RDS_RightSize":
        return [
            step("Snapshot the database", "Create a manual snapshot before any class change.",
                 f"aws rds create-db-snapshot --db-instance-identifier {res} --db-snapshot-identifier {res}-pre-resize"),
            step("Schedule maintenance", "Apply during the next low-traffic maintenance window.",
                 f"aws rds describe-db-instances --db-instance-identifier {res} --query 'DBInstances[].PreferredMaintenanceWindow'"),
            step("Right-size instance class", "Modify to a correctly-sized class for the real load.",
                 f"aws rds modify-db-instance --db-instance-identifier {res} --db-instance-class db.r6g.xlarge --apply-immediately"),
            step("Enable encryption", "Ensure encryption at rest for compliance (PCI).",
                 f"aws rds describe-db-instances --db-instance-identifier {res} --query 'DBInstances[].StorageEncrypted'"),
            step("Verify & record", "Confirm connections are healthy and log the saving.",
                 f"aws rds wait db-instance-available --db-instance-identifier {res}"),
        ]
    if tgt == "GCP_CloudRun_ScaleDown":
        return [
            step("Reduce min-instances", "Drop always-on instances that are billing idle CPU.",
                 f"gcloud run services update {res} --region {region} --min-instances=1"),
            step("Enable scale-to-zero", "Allow the service to idle at zero between requests.",
                 f"gcloud run services update {res} --region {region} --no-cpu-throttling=false"),
            step("Deploy the new revision", "Roll out and route traffic to the cheaper revision.",
                 f"gcloud run services update-traffic {res} --region {region} --to-latest"),
            step("Validate cold-starts", "Confirm p95 latency stays within SLO after scale-down.",
                 f"gcloud monitoring time-series list --filter 'resource.type=cloud_run_revision'"),
            step("Record savings", "Log the recovered always-on CPU spend.",
                 f"gcloud logging write finguard '{res} scaled down'"),
        ]
    if tgt == "GCP_GKE_ScaleToZero":
        return [
            step("Cordon the node pool", "Stop new pods from scheduling onto the idle pool.",
                 f"kubectl cordon -l cloud.google.com/gke-nodepool={res}"),
            step("Drain workloads", "Evict pods gracefully to other capacity.",
                 f"kubectl drain -l cloud.google.com/gke-nodepool={res} --ignore-daemonsets --delete-emptydir-data"),
            step("Set autoscaling min=0", "Let the idle GPU/CPU pool scale to zero nodes.",
                 f"gcloud container clusters update {res} --enable-autoscaling --min-nodes=0 --max-nodes=10 --region {region}"),
            step("Confirm 0 nodes", "Verify the pool has scaled down.",
                 f"gcloud container node-pools describe {res} --region {region}"),
            step("Record savings", "Log the recovered node spend.", f"gcloud logging write finguard '{res} scaled to zero'"),
        ]
    if tgt == "Azure_VM_Resize":
        return [
            step("Snapshot disks", "Snapshot OS & data disks for rollback.",
                 f"az snapshot create -g <rg> -n {res}-snap --source {ref}"),
            step("Schedule window", "Resize requires a deallocate; pick a maintenance window.",
                 f"az vm get-instance-view --ids {ref} --query 'instanceView.statuses'"),
            step("Deallocate the VM", "Stop-deallocate before changing size.", f"az vm deallocate --ids {ref}"),
            step("Resize & start", "Apply the right-sized SKU and restart.",
                 f"az vm resize --ids {ref} --size Standard_D8s_v5 && az vm start --ids {ref}"),
            step("Verify health", "Confirm the app is healthy on the smaller SKU.",
                 f"az vm get-instance-view --ids {ref} --query 'instanceView.statuses[1].displayStatus'"),
        ]
    if tgt == "Azure_Pause_Pool":
        return [
            step("Verify no active jobs", "Ensure no queries/jobs are running before pausing.",
                 f"az synapse sql pool show --name {res} --query 'status'"),
            step("Attach auto-pause", "Add an auto-pause policy so it never idles billed again.",
                 f"az sql db update --name {res} --auto-pause-delay 60"),
            step("Pause the pool", "Pause the warehouse / SQL pool now.", f"az synapse sql pool pause --name {res}"),
            step("Confirm paused", "Verify the paused state.", f"az synapse sql pool show --name {res} --query 'status'"),
            step("Record savings", "Log the recovered compute spend.", "echo 'paused %s'" % res),
        ]
    if tgt == "Delete_Orphaned_Resource":
        cmd = {
            "AWS": f"aws ec2 delete-volume --volume-id {ref}",
            "Azure": f"az resource delete --ids {ref}",
            "GCP": f"gcloud compute disks delete {res} --zone {region} --quiet",
        }.get(cloud, f"delete {ref}")
        return [
            step("Confirm orphaned", "Double-check the resource is unattached and has no dependents.",
                 f"# inspect attachments for {res}"),
            step("Final backup", "Snapshot before deletion as a safety net.",
                 f"# snapshot {res} before delete"),
            step("Delete the resource", "Remove the orphaned resource.", cmd),
            step("Verify deletion", "Confirm the resource no longer exists.", f"# verify {res} is gone"),
            step("Record savings", "Log the recovered spend.", f"echo 'deleted {res}'"),
        ]
    if tgt == "Storage_Lifecycle_Tier":
        cmd = {
            "AWS": f"aws s3api put-bucket-lifecycle-configuration --bucket {res} --lifecycle-configuration file://glacier.json",
            "Azure": f"az storage account management-policy create --account-name {res} --policy @archive.json",
            "GCP": f"gcloud storage buckets update gs://{res} --lifecycle-file=archive.json",
        }.get(cloud, f"apply lifecycle policy to {res}")
        return [
            step("Analyze access patterns", "Confirm objects are cold enough to tier down.",
                 f"# last-access analysis for {res}"),
            step("Define lifecycle policy", "Author a transition rule to the cheaper/archive tier.",
                 "# author lifecycle rule -> Archive/Glacier"),
            step("Apply the policy", "Attach the lifecycle policy to the bucket/account.", cmd),
            step("Verify transition", "Confirm objects begin transitioning.", f"# verify tiering on {res}"),
            step("Record savings", "Log the recovered storage spend.", f"echo 'tiered {res}'"),
        ]
    if tgt == "Enable_Encryption":
        return [
            step("Snapshot data", "Back up before enabling encryption.", f"# snapshot {res}"),
            step("Create/assign KMS key", "Provision a customer-managed key.", f"# create KMS key for {res}"),
            step("Enable encryption at rest", "Turn on encryption with the key.", f"# enable encryption on {ref}"),
            step("Validate read/write", "Confirm the workload still reads/writes correctly.", f"# smoke test {res}"),
            step("Record compliance", "Mark the resource compliant.", f"echo 'encrypted {res}'"),
        ]
    if tgt == "No_Action":
        return [
            step("No remediation required", "Jev judged this negligible / below policy threshold.", "# no-op"),
            step("Log the decision", "Record the rationale for audit.", f"echo 'no action on {res}'"),
            step("Close the ticket", "Resolve the finding.", "# close ticket"),
        ]
    return [
        step("Validate the finding", "Re-check against live telemetry before acting.", f"# validate {res}"),
        step("Prepare rollback", "Create a backup/rollback point.", f"# backup {res}"),
        step("Apply remediation", f"Execute {tgt}.", f"# apply {tgt} to {ref}"),
        step("Verify health", "Confirm the resource is healthy post-change.", f"# verify {res}"),
        step("Record savings", "Log the recovered spend.", f"echo 'remediated {res}'"),
    ]


def _fake_result(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ("snapshot", "backup", "restore point", "kms")):
        return "backup artifact created"
    if any(w in t for w in ("stop", "deallocate")):
        return "state → stopped"
    if "pause" in t:
        return "state → paused"
    if "delete" in t:
        return "resource deleted"
    if any(w in t for w in ("throttle", "concurrency")):
        return "concurrency set to 0"
    if any(w in t for w in ("resize", "right-size", "scale", "min-instances", "autoscaling")):
        return "applied"
    if any(w in t for w in ("encrypt", "compliance")):
        return "encryption enabled"
    if any(w in t for w in ("lifecycle", "policy", "transition", "tier")):
        return "policy applied"
    if any(w in t for w in ("verify", "confirm", "validate", "health", "pre-flight", "analyze", "check")):
        return "verification passed"
    if any(w in t for w in ("record", "tag", "log", "ticket", "rca")):
        return "logged"
    if "drain" in t:
        return "workloads evicted"
    return "ok"


def _hx(seed: str, n: int = 12) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:n]


def _esc(s: str) -> str:
    return html.escape(str(s))


def _fake_stdout(cmd: str, x: Dict[str, Any]) -> List[str]:
    """Realistic-looking CLI stdout for a given remediation command."""
    c = cmd.lower().strip()
    res = x["resource"]
    ref = _resource_ref(x)
    short = ref.split("/")[-1].split(":")[-1]
    region = x.get("region") or "us-east-1"
    sid = _hx(res + cmd, 17)

    if c.startswith("#") or c.startswith("watch") or "noop" in c:
        return ["ok"]
    if any(k in c for k in ("create-image", "create-db-snapshot", "snapshot create", "snapshot ", "backup", "kms key")):
        return [f"Initiating backup for {short} ...",
                f'{{ "SnapshotId": "snap-0{_hx(sid,12)}", "State": "pending", "Encrypted": true }}',
                "State: completed  (100%)"]
    if "stop-instances" in c:
        return [f'{{ "StoppingInstances": [ {{ "InstanceId": "{short}", "CurrentState": {{ "Code": 64, "Name": "stopping" }}, "PreviousState": {{ "Name": "running" }} }} ] }}']
    if "deallocate" in c:
        return ["Deallocating virtual machine ...", '{ "status": "Succeeded", "powerState": "VM deallocated" }']
    if "wait" in c and ("stopped" in c or "instance" in c):
        return ["Waiter InstanceStopped: polling ec2:DescribeInstances", "....", "state -> stopped"]
    if "put-function-concurrency" in c:
        return ['{ "ReservedConcurrentExecutions": 0 }', "throttle applied: new invocations blocked"]
    if "list-event-source-mappings" in c:
        return ['{ "EventSourceMappings": [ { "UUID": "' + _hx(res, 8) + '", "State": "Disabling" } ] }']
    if "get-metric-statistics" in c:
        return ["Datapoints: Invocations(5m) = 0, Errors(5m) = 0", "traffic drained"]
    if "modify-db-instance" in c:
        return ['{ "DBInstance": { "DBInstanceStatus": "modifying", "PendingModifiedValues": { "DBInstanceClass": "db.r6g.xlarge" } } }']
    if "wait db-instance-available" in c or ("rds" in c and "available" in c):
        return ["Waiter DBInstanceAvailable: polling rds:DescribeDBInstances", "....", "status -> available"]
    if "gcloud run services update-traffic" in c:
        return ["Setting traffic -> LATEST ...", "Done. 100% traffic now on newest revision."]
    if "gcloud run services update" in c:
        return [f"Updating service [{res}] ...", "..done.", "Revision deployed: " + res + "-0" + _hx(res, 4)]
    if "kubectl cordon" in c:
        return [f"node/{res}-pool-{_hx(res,4)} cordoned"]
    if "kubectl drain" in c:
        return ["evicting pod default/worker-" + _hx(res, 5),
                "evicting pod kube-system/metrics-" + _hx(res + "m", 5),
                "node drained: 7 pods evicted, 0 errors"]
    if "container clusters update" in c or "node-pools" in c or "container node-pools" in c:
        return [f"Updating cluster [{res}] ...", "..done.", "Node pool autoscaling: min=0 max=10"]
    if "az vm resize" in c:
        return ["Resizing VM to Standard_D8s_v5 ...", '{ "provisioningState": "Succeeded" }']
    if "az vm start" in c:
        return ['{ "powerState": "VM running" }']
    if "synapse sql pool pause" in c or "sql pool pause" in c:
        return [f"Pausing SQL pool [{res}] ...", '{ "status": "Paused" }']
    if "auto-pause" in c or "auto-pause-delay" in c:
        return ['{ "autoPauseDelay": 60, "status": "Online" }']
    if "delete-volume" in c or "disks delete" in c or "resource delete" in c:
        return [f"Deleting {short} ...", '{ "status": "Succeeded" }', "resource no longer exists"]
    if "lifecycle" in c or "management-policy" in c or "buckets update" in c:
        return ["Applying lifecycle policy ...", '{ "Rules": [ { "Status": "Enabled", "Transition": { "StorageClass": "GLACIER", "Days": 30 } } ] }']
    if "create-tags" in c or c.startswith("echo"):
        return ["ok"]
    if "describe-instances" in c or "get-instance-view" in c or "describe-db-instances" in c:
        return [f"Querying {short} ...", '[ "available" ]']
    if "jira create" in c:
        return ["Created issue OPS-" + str(1000 + int(_hx(res, 4), 16) % 9000)]
    if "monitoring time-series" in c or "logging write" in c:
        return ["ok"]
    return ["executing ...", "ok"]


def _explanation(x: Dict[str, Any]) -> str:
    return (
        f"This **{x['waste_category']}** {x['service']} resource (`{x['resource']}` in "
        f"{x['region']}, env `{x['environment']}`) is projected to waste "
        f"**${x['waste']:,.0f}/mo** at ~{x['utilization_pct']:.0f}% utilization. Jev scored "
        f"severity **{x['severity']}/5** and selected **`{x['target']}`** "
        f"({_lane_display(x['lane'])} — {x['reason']})."
    )


def _status_label(x: Dict[str, Any]) -> str:
    rem = st.session_state.get("fg_remediated", {})
    key = f"{x['cloud']}/{x['resource']}"
    if key in rem:
        return "✅ remediated"
    st_key = f"remstatus_{x['cloud']}_{x['resource']}"
    return {"running": "⏳ in progress"}.get(st.session_state.get(st_key, "ready"), "• pending")


def _set_state(key: str, val: str) -> None:
    st.session_state[key] = val


def _step_seconds(title: str) -> int:
    """Realistic wall-clock a real cloud operation would take for this step."""
    t = title.lower()
    if any(w in t for w in ("snapshot", "backup", "restore point", "image", "ami", "kms key")):
        return 45
    if any(w in t for w in ("encrypt", "compliance")):
        return 90
    if "drain" in t:
        return 60
    if any(w in t for w in ("resize", "modify", "scale", "autoscaling", "min-instances", "revision", "deploy")):
        return 40
    if any(w in t for w in ("delete", "remove")):
        return 25
    if any(w in t for w in ("stop", "deallocate", "pause", "cordon")):
        return 20
    if any(w in t for w in ("lifecycle", "policy", "transition", "tier", "trigger")):
        return 18
    if any(w in t for w in ("verify", "confirm", "validate", "wait", "health", "pre-flight", "analyze", "check", "schedule", "drain in-flight")):
        return 15
    if any(w in t for w in ("throttle", "concurrency")):
        return 8
    if any(w in t for w in ("tag", "record", "log", "ticket", "rca", "close", "no-op", "no remediation")):
        return 3
    return 12


def _fmt(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


@st.dialog("Remediation workflow", width="large")
def _remediation_dialog(x: Dict[str, Any]) -> None:
    key = f"remstatus_{x['cloud']}_{x['resource']}"
    status = st.session_state.get(key, "ready")
    plan = _plan(x)

    total_s = sum(_step_seconds(s["title"]) for s in plan)
    st.markdown(f"**Resource:** `{x['resource']}` · {x['cloud']} · {x['service']}  ·  **{_lane_display(x['lane'])}**")
    st.markdown(f"**Recommendation:** {_one_liner(x)}")
    st.markdown(
        f"**Action:** `{x['target']}`  |  **Savings:** ${x['est_savings']:,.0f}/mo  |  "
        f"**Est. runtime:** {_fmt(total_s)} across {len(plan)} steps"
    )
    st.divider()

    if status == "ready":
        st.markdown("**Remediation plan**")
        for i, s in enumerate(plan, 1):
            st.markdown(f"**{i}. {s['title']}** — {s['detail']}  ·  _~{_fmt(_step_seconds(s['title']))}_")
            st.code(s["cmd"], language="bash")
        st.button("▶ Start remediation", type="primary", on_click=_set_state, args=(key, "running"))

    elif status == "running":
        overall = st.progress(0.0, text=f"Executing {len(plan)} steps · est. {_fmt(total_s)}")
        run_id = _hx(x["resource"] + x["target"], 10)
        st.markdown(
            "<div class='fg-termbar'><span class='dot d1'></span><span class='dot d2'></span>"
            f"<span class='dot d3'></span>&nbsp;&nbsp;finguard-remediator — {_esc(x['target'])} @ {_esc(x['cloud'])}</div>",
            unsafe_allow_html=True,
        )
        term = st.empty()
        lines: List[str] = [
            f"<span class='c-dim'>finguard-remediator 2.4.1 · run {run_id} · region {_esc(x.get('region') or '-')}</span>",
            f"<span class='c-dim'>target={_esc(x['target'])}  resource={_esc(x['resource'])}  mode=enforce  approver=finops-operator</span>",
            f"<span class='c-dim'>authenticating to {_esc(x['cloud'].lower())} control plane … OK  (sts session {_hx(run_id, 8)})</span>",
            f"<span class='c-dim'>{'─' * 60}</span>",
        ]

        def flush(cursor: bool = True) -> None:
            tail = "<span class='c-cursor'>▋</span>" if cursor else ""
            term.markdown("<div class='fg-term'>" + "\n".join(lines) + tail + "</div>", unsafe_allow_html=True)

        flush()
        done_s = 0
        for i, s in enumerate(plan, 1):
            dur = _step_seconds(s["title"])
            lines.append(f"<span class='c-dim'># [{i}/{len(plan)}] {_esc(s['title'])}  (eta ~{_fmt(dur)})</span>")
            lines.append(f"<span class='c-prompt'>$</span> <span class='c-cmd'>{_esc(s['cmd'])}</span>")
            flush()
            time.sleep(0.45)
            outs = _fake_stdout(s["cmd"], x)
            for out in outs:
                lines.append(f"<span class='c-out'>  {_esc(out)}</span>")
                flush()
                time.sleep(min(0.3, 1.2 / max(len(outs), 1)))
            lines.append(
                f"<span class='c-ok'>  ✔ {_esc(_fake_result(s['title']))}</span>"
                f"<span class='c-dim'>  — exit 0 · {_fmt(dur)}</span>"
            )
            lines.append("")
            flush()
            done_s += dur
            overall.progress(done_s / total_s, text=f"{_fmt(done_s)} / {_fmt(total_s)}")
        lines.append(
            f"<span class='c-ok'>✔ remediation complete — {len(plan)} steps · 0 errors · runtime {_fmt(total_s)}</span>"
        )
        flush(cursor=False)
        st.success(f"Executed {len(plan)} steps in {_fmt(total_s)} — awaiting operator sign-off.")
        c1, c2 = st.columns(2)
        c1.button("⏹ End & verify remediation", type="primary", on_click=_set_state, args=(key, "done"))
        c2.button("Cancel", on_click=_set_state, args=(key, "ready"))

    else:  # done
        rem = st.session_state.setdefault("fg_remediated", {})
        rem[f"{x['cloud']}/{x['resource']}"] = x["est_savings"]
        st.success(f"Remediation complete — `{x['target']}` applied to `{x['resource']}`.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Monthly savings realized", f"${x['est_savings']:,.0f}")
        m2.metric("Annualized", f"${x['est_savings']*12:,.0f}")
        m3.metric("Runtime", _fmt(total_s))
        st.code(
            f"action: {x['target']}\nresource: {x['resource']}\ncloud: {x['cloud']}\nregion: {x['region']}\n"
            f"steps_executed: {len(plan)}\nruntime: {_fmt(total_s)}\nstatus: SUCCESS\n"
            f"monthly_savings_usd: {x['est_savings']:.0f}",
            language="yaml",
        )
        st.caption("Close this dialog to return to the queue — the row is now marked remediated.")
        st.button("Reset this workflow", on_click=_set_state, args=(key, "ready"))
