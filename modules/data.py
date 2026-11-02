"""Shared data layer: multi-cloud log ingestion, normalization, cost model, corpora."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

AZURE_LOG_FILE = os.path.join(LOGS_DIR, "azure_log_analytics_export.json")
AWS_LOG_FILE = os.path.join(LOGS_DIR, "aws_cur_cloudwatch_export.json")
GCP_LOG_FILE = os.path.join(LOGS_DIR, "gcp_cloud_logging_billing_export.json")

SOURCES = {
    "Azure — Log Analytics export": ("Azure", AZURE_LOG_FILE),
    "AWS — CUR + CloudWatch export": ("AWS", AWS_LOG_FILE),
    "GCP — Cloud Logging + Billing export": ("GCP", GCP_LOG_FILE),
}

# Canonical waste categories used across the governance UI.
WASTE_CATEGORIES = [
    "Idle",
    "Oversized",
    "Orphaned",
    "UnencryptedIdle",
    "RunawayLoop",
    "ColdStorage",
    "InefficientRouting",
    "ObservabilityWaste",
]


@lru_cache(maxsize=8)
def _load(path: str) -> tuple:
    with open(path, "r", encoding="utf-8") as fh:
        return tuple(json.load(fh))


def load_azure_logs() -> List[Dict[str, Any]]:
    return list(_load(AZURE_LOG_FILE))


def load_aws_logs() -> List[Dict[str, Any]]:
    return list(_load(AWS_LOG_FILE))


def load_gcp_logs() -> List[Dict[str, Any]]:
    return list(_load(GCP_LOG_FILE))


# --------------------------------------------------------------------------- #
# Service-category mapping (per provider resource type / product code)
# --------------------------------------------------------------------------- #
def _service_category(cloud: str, key: str) -> str:
    k = (key or "").lower()
    buckets = {
        "Compute": ["virtualmachine", "amazonec2", "gce_instance", "scaleset", "vmss"],
        "Kubernetes": ["managedcluster", "amazoneks", "k8s_cluster", "container", "aks", "eks", "gke"],
        "AI / ML": ["cognitiveservices", "bedrock", "sagemaker", "machinelearning",
                     "aiplatform", "vertex", "gpu"],
        "Networking": ["network", "publicip", "expressroute", "vpn", "frontdoor", "cdn",
                        "loadbalanc", "natgateway", "nat_gateway", "transitgateway", "cloudfront",
                        "elasticip", "reserved_address", "bastion", "applicationgateway", "amazonvpc",
                        "transit"],
        "Storage": ["storage", "amazons3", "amazonebs", "disk", "gcs_bucket", "gce_disk", "snapshot"],
        "Database": ["sql", "rds", "documentdb", "dynamodb", "redshift", "synapse", "cloudsql",
                      "redis", "elasticache", "memorystore", "opensearch", "bigquery"],
        "Serverless": ["lambda", "serverfarms", "web", "cloud_run", "run.googleapis",
                        "cloudfunctions", "cloud_function", "fargate", "amazonecs"],
        "Analytics / Streaming": ["eventhub", "kinesis", "dataflow", "databricks", "emr",
                                    "operationalinsights", "pubsub"],
    }
    for cat, needles in buckets.items():
        if any(n in k for n in needles):
            return cat
    return "Other"


# --------------------------------------------------------------------------- #
# Normalization: flatten each provider record into a common shape
# --------------------------------------------------------------------------- #
def normalize_azure(rec: Dict[str, Any]) -> Dict[str, Any]:
    p = rec.get("Properties", {})
    rtype = rec.get("ResourceType", "")
    provider = rec.get("ResourceProvider", "")
    return _finalize(
        cloud="Azure",
        resource=rec.get("Resource"),
        resource_type=rtype,
        category=_service_category("Azure", f"{provider} {rtype}"),
        region=rec.get("Location"),
        sku=rec.get("SKU"),
        level=rec.get("Level"),
        environment=p.get("environment"),
        team=p.get("team"),
        utilization=p.get("utilizationPct"),
        waste_category=p.get("wasteCategory"),
        waste=p.get("projectedMonthlyWasteUSD"),
        recommended=p.get("recommendedAction"),
        raw=rec,
    )


def normalize_aws(rec: Dict[str, Any]) -> Dict[str, Any]:
    product = rec.get("lineItem/ProductCode", "")
    rid = rec.get("lineItem/ResourceId", "")
    return _finalize(
        cloud="AWS",
        resource=rid.split("/")[-1].split(":")[-1] or product,
        resource_type=product,
        category=_service_category("AWS", product),
        region=rec.get("product/region"),
        sku=rec.get("product/instanceType") or rec.get("product/productFamily") or rec.get("product/model"),
        level="Critical" if rec.get("cloudwatch/RecursiveInvocationDetected") else "Warning",
        environment=rec.get("resourceTags/user:Environment"),
        team=rec.get("resourceTags/user:Owner"),
        utilization=rec.get("finguard/UtilizationPct"),
        waste_category=rec.get("finguard/WasteCategory"),
        waste=rec.get("finguard/ProjectedMonthlyWasteUSD"),
        recommended=rec.get("finguard/RecommendedAction"),
        raw=rec,
    )


def normalize_gcp(rec: Dict[str, Any]) -> Dict[str, Any]:
    fg = rec.get("finguard", {})
    res = rec.get("resource", {})
    labels = res.get("labels", {})
    resource_id = (
        labels.get("instance_id")
        or labels.get("cluster_name")
        or labels.get("database_id")
        or labels.get("endpoint_id")
        or labels.get("service_name")
        or labels.get("bucket_name")
        or labels.get("disk_id")
        or labels.get("function_name")
        or labels.get("job_name")
        or labels.get("gateway_name")
        or labels.get("address_name")
        or labels.get("instance_id")
        or res.get("type")
    )
    return _finalize(
        cloud="GCP",
        resource=resource_id,
        resource_type=res.get("type"),
        category=_service_category("GCP", f"{res.get('type','')} {fg.get('service','')}"),
        region=labels.get("region") or labels.get("zone") or labels.get("location"),
        sku=rec.get("sku"),
        level=rec.get("severity"),
        environment=fg.get("environment"),
        team=fg.get("team"),
        utilization=fg.get("utilizationPct"),
        waste_category=fg.get("wasteCategory"),
        waste=fg.get("projectedMonthlyWasteUSD"),
        recommended=fg.get("recommendedAction"),
        raw=rec,
    )


def _finalize(**kw: Any) -> Dict[str, Any]:
    kw["monthly_waste_usd"] = float(kw.pop("waste", 0.0) or 0.0)
    kw["utilization_pct"] = float(kw.pop("utilization", 0.0) or 0.0)
    kw["environment"] = kw.get("environment") or "unknown"
    kw["team"] = kw.get("team") or "unassigned"
    kw["waste_category"] = kw.get("waste_category") or "Other"
    kw["recommended_action"] = kw.pop("recommended", "") or ""
    return kw


_NORMALIZERS = {
    "Azure": normalize_azure,
    "AWS": normalize_aws,
    "GCP": normalize_gcp,
}


def normalize(cloud: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    return _NORMALIZERS[cloud](rec)


def all_normalized(clouds: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    clouds = clouds or ["Azure", "AWS", "GCP"]
    out: List[Dict[str, Any]] = []
    loaders = {"Azure": load_azure_logs, "AWS": load_aws_logs, "GCP": load_gcp_logs}
    for c in clouds:
        for rec in loaders[c]():
            out.append(normalize(c, rec))
    return out


def ingest_uploaded(cloud: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize user-uploaded provider records (best-effort)."""
    norm = _NORMALIZERS.get(cloud, normalize_azure)
    out = []
    for rec in records:
        try:
            out.append(norm(rec))
        except Exception:  # noqa: BLE001 — skip malformed rows
            continue
    return out


# --------------------------------------------------------------------------- #
# Cost model — the "with vs without Jev" simulation
# --------------------------------------------------------------------------- #
JEV_PRICE_PER_MTOK_INPUT = 0.042  # ~$0.042 / 1M input tokens, output free

DEFAULT_AUTO_REMEDIATE_RATE = 0.70
DEFAULT_ESCALATION_RECOVERY_RATE = 0.45

# Realistic recoverable fraction of the projected waste per category — how much
# of the bleed FinGuard can actually claw back once the action is applied.
CAPTURE_BY_CATEGORY: Dict[str, float] = {
    "Orphaned": 0.98,            # just delete it
    "RunawayLoop": 0.95,         # kill the loop
    "Idle": 0.90,                # stop / pause
    "ColdStorage": 0.85,         # lifecycle to cheaper tier
    "UnencryptedIdle": 0.80,     # right-size + encrypt
    "Oversized": 0.70,           # right-size (some headroom kept)
    "InefficientRouting": 0.70,  # route to cheaper model/tier
    "ObservabilityWaste": 0.60,  # drop verbose logs
    "Other": 0.50,
}

# Avg input tokens Jev reads per decision, and how often the estate is re-scanned.
AVG_TOKENS_PER_DECISION = 330
RESCANS_PER_DAY = 96            # every 15 minutes
DAYS_PER_MONTH = 30


def jev_operating_cost(num_resources: int) -> Dict[str, float]:
    """Monthly cost of running Jev continuously over the estate (realistic + tiny)."""
    decisions = num_resources * RESCANS_PER_DAY * DAYS_PER_MONTH
    input_mtok = decisions * AVG_TOKENS_PER_DECISION / 1_000_000.0
    monthly = input_mtok * JEV_PRICE_PER_MTOK_INPUT
    return {"decisions": decisions, "input_mtok": input_mtok, "monthly_usd": monthly}


def benefits_breakdown(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Realistic savings derived directly from the Azure / AWS / GCP log data."""
    rows = rows if rows is not None else all_normalized()
    total_waste = 0.0
    recoverable = 0.0
    by_cloud_waste: Dict[str, float] = {}
    by_cloud_save: Dict[str, float] = {}
    by_cat_save: Dict[str, float] = {}
    for r in rows:
        w = r["monthly_waste_usd"]
        cap = CAPTURE_BY_CATEGORY.get(r["waste_category"], 0.5)
        s = w * cap
        total_waste += w
        recoverable += s
        by_cloud_waste[r["cloud"]] = by_cloud_waste.get(r["cloud"], 0.0) + w
        by_cloud_save[r["cloud"]] = by_cloud_save.get(r["cloud"], 0.0) + s
        by_cat_save[r["waste_category"]] = by_cat_save.get(r["waste_category"], 0.0) + s

    jev = jev_operating_cost(len(rows))
    net = recoverable - jev["monthly_usd"]
    return {
        "n": len(rows),
        "total_waste": total_waste,
        "recoverable": recoverable,
        "net": net,
        "jev_cost": jev["monthly_usd"],
        "jev_decisions": jev["decisions"],
        "roi": (recoverable / jev["monthly_usd"]) if jev["monthly_usd"] else 0.0,
        "pct_recoverable": (recoverable / total_waste * 100.0) if total_waste else 0.0,
        "by_cloud_waste": by_cloud_waste,
        "by_cloud_save": by_cloud_save,
        "by_cat_save": by_cat_save,
    }


def waste_summary(rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    rows = rows if rows is not None else all_normalized()
    total = sum(r["monthly_waste_usd"] for r in rows)
    by_cloud: Dict[str, float] = {}
    by_category: Dict[str, float] = {}
    by_service: Dict[str, float] = {}
    by_env: Dict[str, float] = {}
    for r in rows:
        by_cloud[r["cloud"]] = by_cloud.get(r["cloud"], 0.0) + r["monthly_waste_usd"]
        by_category[r["waste_category"]] = by_category.get(r["waste_category"], 0.0) + r["monthly_waste_usd"]
        by_service[r["category"]] = by_service.get(r["category"], 0.0) + r["monthly_waste_usd"]
        by_env[r["environment"]] = by_env.get(r["environment"], 0.0) + r["monthly_waste_usd"]
    return {
        "rows": rows,
        "total_monthly_waste": total,
        "by_cloud": by_cloud,
        "by_category": by_category,
        "by_service": by_service,
        "by_env": by_env,
    }


def simulate_savings(
    monthly_waste: float,
    decisions_per_month: int = 50_000_000,
    avg_tokens_per_decision: int = 220,
    auto_rate: float = DEFAULT_AUTO_REMEDIATE_RATE,
    escalation_rate: float = DEFAULT_ESCALATION_RECOVERY_RATE,
    inference_spend: float = 180_000.0,
    inference_route_savings: float = 0.70,
    routable_simple_ratio: float = 0.78,
) -> Dict[str, Any]:
    auto_saved = monthly_waste * auto_rate
    remaining_after_auto = monthly_waste - auto_saved
    escalated_saved = remaining_after_auto * escalation_rate
    remediation_saved = auto_saved + escalated_saved

    inference_saved = inference_spend * routable_simple_ratio * inference_route_savings

    input_mtok = (decisions_per_month * avg_tokens_per_decision) / 1_000_000.0
    jev_cost = input_mtok * JEV_PRICE_PER_MTOK_INPUT

    gross_saved = remediation_saved + inference_saved
    net_saved = gross_saved - jev_cost
    without = monthly_waste + inference_spend
    with_jev = without - gross_saved + jev_cost

    return {
        "without_jev_monthly": without,
        "with_jev_monthly": with_jev,
        "monthly_waste": monthly_waste,
        "inference_spend": inference_spend,
        "auto_saved": auto_saved,
        "escalated_saved": escalated_saved,
        "remediation_saved": remediation_saved,
        "inference_saved": inference_saved,
        "jev_cost": jev_cost,
        "jev_input_mtok": input_mtok,
        "gross_saved": gross_saved,
        "net_saved": net_saved,
        "roi_multiple": (gross_saved / jev_cost) if jev_cost else 0.0,
        "pct_reduction": (gross_saved / without * 100.0) if without else 0.0,
    }


# --------------------------------------------------------------------------- #
# Module 1 demo corpus
# --------------------------------------------------------------------------- #
SAMPLE_PROMPTS: List[Dict[str, str]] = [
    {"label": "Trivial format fix", "text": "Capitalize the first letter of this product name: 'wireless earbuds pro'."},
    {"label": "Simple extraction", "text": "Extract the email address from: 'Contact me at jane.doe@acme.io for details.'"},
    {"label": "Short classification", "text": "Is this review positive or negative? 'Shipping was slow but the item is great.'"},
    {"label": "Standard summary", "text": "Summarize this 4-paragraph incident report into 3 bullet points for a status page."},
    {"label": "Code refactor (mid)", "text": "Refactor this 40-line Python function to remove duplicated validation logic and add type hints."},
    {"label": "Complex reasoning", "text": "Design a multi-region active-active failover strategy for a Postgres + Kafka system with an RPO of 5 seconds and justify the trade-offs."},
    {"label": "Math / proof (premium)", "text": "Prove that the sum of the first n odd numbers equals n^2, then generalize to an arithmetic series and derive the closed form."},
]

ROUTE_TARGETS = {
    "Simple/Edge": {"model": "local Llama-3.1-8B on EC2 spot / Cloudflare Workers AI", "rel_cost": 0.10, "color": "#2ecc71"},
    "Standard/Mid-Tier": {"model": "GPT-4o-mini / Claude Haiku", "rel_cost": 0.30, "color": "#f1c40f"},
    "Reasoning/Premium": {"model": "GPT-4o / Claude 3.5 Sonnet", "rel_cost": 1.00, "color": "#e74c3c"},
}


# --------------------------------------------------------------------------- #
# Module 3 demo corpus — Terraform / Pulumi plans
# --------------------------------------------------------------------------- #
SAMPLE_IAC: List[Dict[str, str]] = [
    {
        "label": "QA: multi-region HA Postgres (should BLOCK)",
        "metadata": "Deploying an ultra-high availability, multi-region replicated database cluster for the qa-environment.",
        "plan": (
            'resource "aws_rds_cluster" "qa" {\n'
            '  engine                = "aurora-postgresql"\n'
            '  instance_class        = "db.r6g.16xlarge"\n'
            '  availability_zones    = ["us-east-1a","us-east-1b","us-east-1c"]\n'
            '  global_cluster        = true\n'
            '  replication_regions   = ["us-west-2","eu-west-1"]\n'
            '  backup_retention      = 35\n'
            '  tags = { Environment = "qa" }\n'
            '}'
        ),
    },
    {
        "label": "Prod: right-sized web tier (should PASS)",
        "metadata": "Standard autoscaling web tier for the production storefront, 2-6 instances.",
        "plan": (
            'resource "aws_autoscaling_group" "web" {\n'
            '  min_size         = 2\n'
            '  max_size         = 6\n'
            '  instance_type    = "t3.large"\n'
            '  tags = { Environment = "prod" }\n'
            '}'
        ),
    },
    {
        "label": "Dev: 8x A100 GPU pool (should BLOCK)",
        "metadata": "Spinning up a dedicated 8x A100 GPU node pool for a developer sandbox.",
        "plan": (
            'resource "azurerm_kubernetes_cluster_node_pool" "sandbox" {\n'
            '  vm_size    = "Standard_NC24ads_A100_v4"\n'
            '  node_count = 8\n'
            '  tags = { env = "dev" }\n'
            '}'
        ),
    },
    {
        "label": "Staging: small cache (should PASS)",
        "metadata": "Single-node Redis cache for staging feature-flag evaluation.",
        "plan": (
            'resource "aws_elasticache_cluster" "flags" {\n'
            '  engine         = "redis"\n'
            '  node_type      = "cache.t4g.small"\n'
            '  num_cache_nodes = 1\n'
            '  tags = { Environment = "staging" }\n'
            '}'
        ),
    },
]
