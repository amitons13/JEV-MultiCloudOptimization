# 🛡️ FinGuard.ai — The Jev-Powered Multi-Cloud FinOps Micro-Governor

A continuous, low-latency **control loop** that sits between your multi-cloud state
(logs, CUR, IaC) and your AWS · GCP · Azure environment. It intercepts developer
actions, alerts, and runtime requests to **enforce cost efficiency in real time**,
using TypeSafe's **Jev** "System One" router (`typesafe/jev-router`) on OpenRouter
as a split-second decision engine.

```
                 ┌───────────────────┐
                 │ Multi-Cloud State │
                 │ (Logs, CUR, IaC)  │
                 └─────────┬─────────┘
                           ▼
                 ┌───────────────────┐
                 │    Jev Model      │
                 │  (System One)     │
                 └─────────┬─────────┘
        ┌──────────────────┼──────────────────┐
   High-Conf            Mid-Conf            Low-Conf
        ▼                  ▼                  ▼
  Auto-Remediate     Escalate via      Route to Slow
 (Shut Down /        Slack / Jira      Reasoning LLM
  Right-Size /       (Human Review)    (Deep Analysis)
  Cheap Route)
```

## Why Jev?

Traditional text-generation LLMs are too slow (several seconds) and expensive for
raw inline operations. **Jev is a System One split-second decision engine** —
~**$0.042 / 1M input tokens, output free** — so it can sit in the hot path and
answer small, strongly-typed questions:

| Primitive | Meaning | Example |
|-----------|---------|---------|
| **Choice** | pick one from an enum | routing tier / remediation target |
| **Score**  | ordered scale 1–5 | waste severity |
| **Noul**   | yes/no boolean | `Is_Urgent_Action_Required`, `Is_Cost_Policy_Violated` |

Every answer carries a **confidence** that selects the control-loop lane:
high → auto-remediate, mid → escalate to a human, low → slow reasoning LLM.

## Modules

1. **Smart Cost Prompt Router** — Jev classifies each prompt (Simple / Standard /
   Reasoning) and routes it to the cheapest capable model (up to 70% inference savings).
2. **FinOps Control Center** — a staged governance workflow:
   1. **Ingest** multi-cloud logs / CUR (bundled AWS · Azure · GCP exports, or upload your own)
   2. **Configure** a governance policy — confidence thresholds, automation mode
      (Advisory / Dry-run / Enforce), protected environments, allowed actions, severity
      floor, in-scope categories, and savings-capture assumptions
   3. **Analyze** — Jev triages every in-scope resource in one pass (Score + Choice + Noul)
      and the policy decides each lane (auto-remediate / escalate / deep-reason)
   4. **Recommend** — a prioritized, approvable recommendation queue with KPIs, charts,
      an apply/dry-run action log, and CSV export
3. **IaC Financial Guardrails** — a pre-commit / GitHub Action asks Jev
   `Is_Cost_Policy_Violated`; a high-certainty `true` blocks the deploy before provisioning.

Plus a **Cost Calculator** that simulates a multi-cloud estate *without* vs *with* Jev.

## Setup

```bash
# 1. clone / open this folder, then:
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell:  .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. add your OpenRouter key
copy .env.example .env            # then edit .env and paste OPENROUTER_API_KEY

# 3. run
streamlit run app.py
```

Get an OpenRouter key at <https://openrouter.ai/keys>. Without a key the app still
runs in a **deterministic demo-heuristic mode** so you can explore every screen.

### Environment variables (`.env`)

| Var | Default | Purpose |
|-----|---------|---------|
| `OPENROUTER_API_KEY` | — | your OpenRouter key (`sk-or-...`) |
| `JEV_MODEL` | `typesafe/jev-router` | the Jev System One model |
| `REASONING_MODEL` | `anthropic/claude-3.5-sonnet` | slow-path deep analysis |

## Data

`logs/` contains **60 synthetic anomalous resources** across three providers and every
major service category (Compute/VMs, Kubernetes AKS/EKS/GKE, Networking, AI/ML, Storage,
Database, Serverless, Analytics/Streaming):

| File | Shape | Records |
|------|-------|---------|
| `azure_log_analytics_export.json` | Azure Log Analytics export | 23 |
| `aws_cur_cloudwatch_export.json` | AWS CUR + CloudWatch | 23 |
| `gcp_cloud_logging_billing_export.json` | GCP Cloud Logging + Billing | 14 |

Scenarios include idle GPUs, unencrypted idle DBs, runaway/recursive loops, orphaned
IPs/disks/snapshots, over-provisioned clusters and caches, cold storage, unpaused
warehouses, and inefficient premium-model routing. Each record is enriched with
`environment`, `team`, `utilization`, `wasteCategory`, projected monthly waste, and a
recommended fix.

## Project layout

```
app.py                     # Streamlit entrypoint + architecture overview
.streamlit/config.toml     # professional dark theme
jev/                       # Jev client over OpenRouter (Choice/Score/Noul)
  ├─ client.py             # JevClient + strict structured outputs + demo fallback
  └─ schemas.py            # Question / QType / confidence bands
modules/                   # the four screens
  ├─ data.py               # multi-cloud ingestion + normalization + cost model
  ├─ ui.py                 # theme, headers, badges, confidence bands
  ├─ prompt_router.py      # Module 1
  ├─ log_triage.py         # Module 2 — FinOps Control Center workflow
  ├─ iac_guardrails.py     # Module 3
  └─ cost_calculator.py    # without-vs-with-Jev simulation
logs/                      # synthetic Azure + AWS + GCP exports
```
