---
name: hiring-signal-tracker
description: Track public tech companies' AI investment through their official job-posting APIs (Greenhouse/Lever/Ashby) and turn hiring data into investment research signals. Use this skill whenever the user wants to collect or update hiring data, run the hiring signal report, add/remove/replace companies in the watchlist (股票池), analyze a company's AI hiring momentum, set up recurring collection, reconcile hiring signals with earnings data, or asks about 招聘信号 / AI 投入跟踪 / 招聘数据分析 — even if they don't name the skill. Also use it when a financial analyst asks how a tech company's expansion or AI strategy shows up in its hiring.
---

# Hiring Signal Tracker

Collects job postings from listed tech companies' official ATS APIs, scores every
job description against a tiered AI-skill dictionary, and accumulates a per-company
time series of AI-investment signals for fundamental (value-investing) research.
The output is research input, never a buy/sell recommendation.

## Directory layout

```
hiring-signal-tracker/
├── SKILL.md              this file
├── README.md             user-facing docs (Chinese)
├── config.json           watchlist + dictionaries + classification rules
├── scripts/
│   ├── pipeline.py       collect → score → time series → HTML report (stdlib only)
│   └── probe.py          detect which ATS a new company uses
├── references/
│   └── methodology.md    investment logic, metric definitions, caveats — READ THIS
│                         before interpreting any numbers for the user
├── data/                 accumulating time series (jobs_*.csv, history.csv, state.json)
└── reports/              generated HTML reports
```

## Core operations

**Run a collection** (the most common request — "跑一次采集 / 更新报告"):

```bash
cd <skill-root> && python3 scripts/pipeline.py
```

Python 3.8+ standard library only; no pip installs. Takes 1–3 minutes for ~27
companies. Same-day reruns overwrite that day's snapshot safely. After running,
open `reports/report_<date>.html` for the user and summarize the notable changes
(see "Interpreting results" below).

**Add or replace companies** ("把 X 加进股票池"):

1. Detect the company's ATS: `python3 scripts/probe.py <guess1> <guess2> ...`
   Try the company's short name, product name, and name without spaces
   (e.g. for HubSpot the working token was `hubspotjobs`, not `hubspot`).
2. If found, add an entry to `companies` in config.json with name, ticker, ats,
   token, and a theme (used for peer grouping in reports — keep peers together).
3. If all probes miss, the company likely uses Workday or a custom system —
   tell the user it cannot be tracked in v1 rather than guessing.
4. Run a collection to establish the company's baseline.

Companies known to be unreachable: Snowflake, CrowdStrike, Zscaler, Atlassian,
UiPath (Workday/custom). Big-tech (Google/Meta/Microsoft/Amazon) is deliberately
excluded: thousands of postings dilute the signal.

**Set up recurring collection** ("每周自动采集"): prefer the host's scheduling
feature if present. Otherwise offer a cron entry (weekly, Monday morning):

```
0 9 * * 1 cd <skill-root> && /usr/bin/python3 scripts/pipeline.py
```

Report delivery by email requires the user's own connected mail tool and their
explicit approval each time — never wire credentials into scripts.

**Edit the skill dictionary** (config.json `skill_categories`): after any
dictionary change, warn the user that density scores are no longer comparable
with earlier snapshots; offer to archive `data/history.csv` into `data/archive/`
and restart the baseline.

## Interpreting results

Read `references/methodology.md` before writing analysis for the user. The
non-negotiable rules it explains:

- **Within-company time series beats cross-company levels.** JD writing styles
  differ by company and ATS; a company's change against its own baseline is the
  signal. Cross-company comparison is only meaningful within the same `theme`.
- **Signal quality ranks by role type**: demand-validated roles (FDE, solutions,
  sales engineering — staffed against real contracts) > product engineering >
  research. Non-tech AI penetration (AI skills required in sales/finance/ops
  roles) marks AI moving from lab to business infrastructure.
- **Negative signals are more reliable than positive ones.** Companies fake
  expansion, never contraction. Lead with red flags (batch job removals) when
  present.
- **Boilerplate suppression**: a term hitting ≥90% of one company's JDs is
  marketing copy, not skill demand — the pipeline removes it automatically and
  lists it in the `boilerplate_suppressed` column. Mention it if a company's
  score shifted because of it.
- Hiring is necessary but not sufficient evidence of value creation: a
  cash-burning expander and a profitable compounder post the same jobs. Always
  push toward reconciliation (below) before drawing conclusions.

## Advanced analysis (agent-run, not scripted)

These are part of the research method; run them with your own web/search tools
when the user asks for deeper work.

**B1 — Earnings reconciliation.** For companies whose signals moved, pull the
latest 10-Q/10-K from SEC EDGAR (`https://www.sec.gov/cgi-bin/browse-edgar`) and
check whether revenue growth, RPO / deferred revenue, and headcount direction
confirm or contradict the hiring signal. A rising demand-validated share with
accelerating RPO is confirmation; with stagnant RPO it may be firefighting, not
expansion.

**B2 — Say-do gap.** Compare management's AI narrative in the latest earnings
call with the hiring data. Specific claims (customer counts, revenue
contribution) + matching hires = credible. Vision language + no AI hiring = flag
the inconsistency to the user; that gap is itself a research finding.

**C — Composite momentum (needs ≥3 snapshots).** Rank companies within theme on
a blend of Δ total postings, Δ weighted density, Δ demand-validated share, and
red-flag count. Present as a research-priority funnel ("deep-dive candidates"),
never as a buy list.

## Compliance boundaries

Only official, public, unauthenticated job-board APIs. Never scrape LinkedIn,
Glassdoor, or Indeed (ToS-prohibited), never bypass rate limits or robots.txt,
and never present output as investment advice — it is one input into
fundamental research done by a human analyst.
