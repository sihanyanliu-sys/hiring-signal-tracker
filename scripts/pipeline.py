#!/usr/bin/env python3
"""hiring-signal-tracker 采集管线 v2

拉取上市公司官方招聘 API -> 计算 AI 投入信号指标 -> 累积时间序列 -> 生成 HTML 报告。
仅用 Python 标准库,无第三方依赖。

用法: python3 scripts/pipeline.py
产物: data/jobs_<date>.csv (职位明细)、data/history.csv (公司级时间序列)、
      data/state.json (已知地点/部门,用于新信号检测)、reports/report_<date>.html
同一天重复运行会覆盖当天数据。
"""
import csv, html, json, re, statistics, sys, urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text())
TODAY = date.today().isoformat()
TAG_RE = re.compile(r"<[^>]+>")
SALARY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK]\b)")

# ---------- 抓取 ----------

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hiring-signal-tracker/2.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

def clean(raw_html):
    return TAG_RE.sub(" ", html.unescape(raw_html or "")).lower()

def fetch_jobs(company):
    ats, token = company["ats"], company["token"]
    jobs = []
    if ats == "greenhouse":
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
        for j in data.get("jobs", []):
            jobs.append({
                "title": j.get("title", ""),
                "department": (j.get("departments") or [{}])[0].get("name") or "",
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "text": clean(j.get("content", "")),
            })
    elif ats == "lever":
        data = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
        for j in data:
            body = (j.get("description") or "") + " ".join(
                (l.get("text", "") + " " + l.get("content", "")) for l in j.get("lists", [])
            ) + (j.get("additional") or "")
            cat = j.get("categories") or {}
            jobs.append({
                "title": j.get("text", ""),
                "department": cat.get("team") or cat.get("department") or "",
                "location": cat.get("location", ""),
                "url": j.get("hostedUrl", ""),
                "text": clean(body),
            })
    elif ats == "ashby":
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
        for j in data.get("jobs", []):
            jobs.append({
                "title": j.get("title", ""),
                "department": j.get("department", "") or j.get("team", ""),
                "location": j.get("location", ""),
                "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                "text": clean(j.get("descriptionHtml", "") or j.get("descriptionPlain", "")),
            })
    return jobs

# ---------- 打分 ----------

CAT_RES = {cat: (spec["weight"], [(re.compile(t), t) for t in spec["terms"]])
           for cat, spec in CFG["skill_categories"].items()}
VENDOR_RES = [(re.compile(t), t) for t in CFG["vendors"]]
ROLE_RES = {b: [re.compile(p) for p in pats] for b, pats in CFG["role_rules"].items()}
SEN_RES = {b: [re.compile(p) for p in pats] for b, pats in CFG["seniority_rules"].items()}
AI_TITLE_RES = [re.compile(p) for p in CFG["ai_title_terms"]]

def scan_job(text):
    """按类别扫描,词条去重。返回 (hits=[(cat, pat, weight)], vendors)"""
    hits = []
    for cat, (w, regexes) in CAT_RES.items():
        for rx, pat in regexes:
            if rx.search(text):
                hits.append((cat, pat, w))
    vendors = [pat for rx, pat in VENDOR_RES if rx.search(text)]
    return hits, vendors

BOILERPLATE_DF = 0.9   # 词条出现在公司 >=90% 的 JD 中视为模板文案
BOILERPLATE_MIN_N = 20 # 职位数太少时不启用抑制,避免误杀

def find_boilerplate(scanned, n):
    """公司内模板词检测:营销文案(如 'BrazeAI' 全员出现)测量的不是岗位技能需求。"""
    if n < BOILERPLATE_MIN_N:
        return set()
    df = Counter(pat for _, hits, _ in scanned for pat in {p for _, p, _ in hits})
    return {pat for pat, k in df.items() if k / n >= BOILERPLATE_DF}

def classify_role(title):
    t = title.lower()
    for bucket in ("demand_validated", "research", "product_eng", "nontech"):
        if any(rx.search(t) for rx in ROLE_RES[bucket]):
            return bucket
    return "other"

def classify_seniority(title):
    t = title.lower()
    for bucket in ("intern", "exec", "director", "staff_principal", "senior"):
        if any(rx.search(t) for rx in SEN_RES[bucket]):
            return bucket
    return "mid"

def extract_salary(text):
    """从 JD 提取美元年薪区间中点;找不到可信数字则返回 None。"""
    lo, hi = CFG["salary"]["min_plausible"], CFG["salary"]["max_plausible"]
    vals = []
    for m in SALARY_RE.finditer(text):
        s = m.group(1).replace(",", "").lower().replace(" ", "")
        v = float(s[:-1]) * 1000 if s.endswith("k") else float(s)
        if lo <= v <= hi:
            vals.append(v)
    if not vals:
        return None
    return round((min(vals) + max(vals)) / 2)

# ---------- 状态与差分 ----------

def load_state():
    p = ROOT / "data" / "state.json"
    return json.loads(p.read_text()) if p.exists() else {"locations": {}, "departments": {}}

def save_state(state):
    (ROOT / "data" / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))

def load_prev_jobs():
    """最近一次(早于今天)的职位快照,按 ticker 分组。"""
    files = sorted(f for f in (ROOT / "data").glob("jobs_*.csv")
                   if f.stem.split("_")[1] < TODAY)
    if not files:
        return None, {}
    by_ticker = {}
    with files[-1].open() as f:
        for r in csv.DictReader(f):
            by_ticker.setdefault(r["ticker"], []).append(r)
    return files[-1].stem.split("_")[1], by_ticker

# ---------- 主流程 ----------

def main():
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    state = load_state()
    prev_date, prev_jobs = load_prev_jobs()
    job_rows, summaries, changes = [], [], {}

    for c in CFG["companies"]:
        try:
            jobs = fetch_jobs(c)
        except Exception as e:
            print(f"[WARN] {c['name']} 抓取失败: {e}", file=sys.stderr)
            continue
        n = len(jobs)
        if n == 0:
            print(f"[WARN] {c['name']} 返回 0 个职位,跳过", file=sys.stderr)
            continue
        scanned = [(j,) + scan_job(j["text"]) for j in jobs]
        boiler = find_boilerplate(scanned, n)
        term_counter, vendor_counter = Counter(), Counter()
        role_counts, sen_counts = Counter(), Counter()
        ai_title = strong = engaged = wsum = nontech_ai = 0
        model_hits = app_hits = 0
        ai_sal, nonai_sal = [], []
        cur_locs, cur_depts, cur_urls = set(), set(), set()

        for j, all_hits, vendors in scanned:
            hits = [(cat, p, w) for cat, p, w in all_hits if p not in boiler]
            weighted = sum(w for _, _, w in hits)
            cats = {cat for cat, _, _ in hits}
            terms = [p for _, p, _ in hits]
            role = classify_role(j["title"])
            sen = classify_seniority(j["title"])
            is_ai_title = any(rx.search(j["title"].lower()) for rx in AI_TITLE_RES)
            is_strong = bool(cats & {"model_infra", "application"})
            is_engaged = bool(cats & {"model_infra", "application", "data_ml_ops", "general_medium"})
            sal = extract_salary(j["text"])
            role_counts[role] += 1
            sen_counts[sen] += 1
            ai_title += is_ai_title
            strong += is_strong
            engaged += is_engaged
            wsum += weighted
            model_hits += "model_infra" in cats
            app_hits += "application" in cats
            if role == "nontech" and is_engaged:
                nontech_ai += 1
            if sal:
                (ai_sal if is_strong or is_ai_title else nonai_sal).append(sal)
            term_counter.update(terms)
            vendor_counter.update(vendors)
            for loc in j["location"].split(";"):
                if loc.strip():
                    cur_locs.add(loc.strip().lower())
            if j["department"]:
                cur_depts.add(j["department"].strip().lower())
            cur_urls.add(j["url"])
            job_rows.append({
                "date": TODAY, "ticker": c["ticker"], "company": c["name"],
                "title": j["title"], "department": j["department"], "location": j["location"],
                "role_bucket": role, "seniority": sen, "ai_title": int(is_ai_title),
                "weighted_score": weighted, "strong_hit": int(is_strong),
                "categories": "|".join(sorted(cats)), "salary_mid": sal or "",
                "url": j["url"],
            })

        # A1 消失职位 + 红旗
        removed, red_flags = 0, []
        if c["ticker"] in prev_jobs:
            prev_rows = prev_jobs[c["ticker"]]
            gone = [r for r in prev_rows if r["url"] not in cur_urls]
            removed = len(gone)
            prev_dept = Counter(r.get("department", "") for r in prev_rows)
            gone_dept = Counter(r.get("department", "") for r in gone)
            for d, k in gone_dept.most_common():
                if d and k >= 5 and k / prev_dept[d] >= 0.3:
                    red_flags.append(f"{d} 部门下架 {k}/{prev_dept[d]} 个职位")
        # A3 新地点 / 新部门(公司首采时只建基线不报新增)
        known_l = set(state["locations"].get(c["ticker"], []))
        known_d = set(state["departments"].get(c["ticker"], []))
        new_locs = sorted(cur_locs - known_l) if known_l else []
        new_depts = sorted(cur_depts - known_d) if known_d else []
        state["locations"][c["ticker"]] = sorted(known_l | cur_locs)
        state["departments"][c["ticker"]] = sorted(known_d | cur_depts)
        changes[c["ticker"]] = {"removed": removed, "red_flags": red_flags,
                                "new_locations": new_locs, "new_departments": new_depts}

        ai_mid = round(statistics.median(ai_sal)) if len(ai_sal) >= 3 else ""
        nonai_mid = round(statistics.median(nonai_sal)) if len(nonai_sal) >= 3 else ""
        premium = round((ai_mid / nonai_mid - 1) * 100, 1) if ai_mid and nonai_mid else ""
        nontech_n = role_counts["nontech"]
        summaries.append({
            "date": TODAY, "ticker": c["ticker"], "company": c["name"], "theme": c["theme"],
            "total_postings": n,
            "ai_title_share": round(ai_title / n * 100, 1),
            "ai_engaged_share": round(engaged / n * 100, 1),
            "weighted_density": round(wsum / n, 2),
            "strong_skill_share": round(strong / n * 100, 1),
            "model_infra_share": round(model_hits / engaged * 100, 1) if engaged else 0,
            "application_share": round(app_hits / engaged * 100, 1) if engaged else 0,
            "demand_validated_share": round(role_counts["demand_validated"] / n * 100, 1),
            "research_jobs": role_counts["research"],
            "nontech_ai_penetration": round(nontech_ai / nontech_n * 100, 1) if nontech_n else 0,
            "exec_dir_share": round((sen_counts["exec"] + sen_counts["director"]) / n * 100, 1),
            "junior_share": round(sen_counts["intern"] / n * 100, 1),
            "salary_coverage": round((len(ai_sal) + len(nonai_sal)) / n * 100, 1),
            "ai_salary_mid": ai_mid, "nonai_salary_mid": nonai_mid, "ai_pay_premium": premium,
            "top_vendors": "; ".join(f"{v}×{k}" for v, k in vendor_counter.most_common(3)),
            "removed_since_prev": removed,
            "new_locations": len(new_locs), "new_departments": len(new_depts),
            "top_terms": "; ".join(f"{t}×{k}" for t, k in term_counter.most_common(5)),
            "boilerplate_suppressed": "; ".join(sorted(boiler)),
        })
        print(f"[OK] {c['name']}: {n} 个职位")

    if not summaries:
        sys.exit("没有任何公司抓取成功")
    save_state(state)

    jobs_path = ROOT / "data" / f"jobs_{TODAY}.csv"
    with jobs_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(job_rows[0].keys()))
        w.writeheader(); w.writerows(job_rows)

    hist_path = ROOT / "data" / "history.csv"
    hist = []
    if hist_path.exists():
        with hist_path.open() as f:
            hist = [r for r in csv.DictReader(f) if r["date"] != TODAY]
    fields = list(summaries[0].keys())
    with hist_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in hist: w.writerow({k: r.get(k, "") for k in fields})
        w.writerows(summaries)

    report(summaries, hist, changes, prev_date)

# ---------- 战略动向雷达:周度跳跃检测 ----------

JUMP_RULES = [
    # (标签, 严重度, 判定函数 cur,prev -> 描述 or None)
    ("收缩预警", 3, lambda c, p: (
        f"在招职位 {p['total_postings']}→{c['total_postings']} ({_rel(c,p,'total_postings'):+.0f}%)"
        if _rel(c, p, "total_postings") <= -10 else None)),
    ("扩张加速", 2, lambda c, p: (
        f"在招职位 {p['total_postings']}→{c['total_postings']} ({_rel(c,p,'total_postings'):+.0f}%)"
        if _rel(c, p, "total_postings") >= 10 else None)),
    ("需求验证跃升", 3, lambda c, p: (
        f"需求验证型岗位占比 {p['demand_validated_share']}%→{c['demand_validated_share']}%"
        if _pp(c, p, "demand_validated_share") >= 5 else None)),
    ("AI 投入跃升", 2, lambda c, p: (
        f"技能密度 {p['weighted_density']}→{c['weighted_density']} ({_rel(c,p,'weighted_density'):+.0f}%)"
        if float(p["weighted_density"]) >= 0.5 and _rel(c, p, "weighted_density") >= 25 else None)),
    ("AI 投入降温", 2, lambda c, p: (
        f"技能密度 {p['weighted_density']}→{c['weighted_density']} ({_rel(c,p,'weighted_density'):+.0f}%)"
        if float(p["weighted_density"]) >= 0.5 and _rel(c, p, "weighted_density") <= -25 else None)),
    ("转向模型自建", 2, lambda c, p: (
        f"AI 职位中模型自建占比 {p['model_infra_share']}%→{c['model_infra_share']}%"
        if _pp(c, p, "model_infra_share") >= 10 else None)),
    ("转向应用集成", 1, lambda c, p: (
        f"AI 职位中模型自建占比 {p['model_infra_share']}%→{c['model_infra_share']}%"
        if _pp(c, p, "model_infra_share") <= -10 else None)),
    ("高管招聘潮", 2, lambda c, p: (
        f"高管+总监岗位占比 {p['exec_dir_share']}%→{c['exec_dir_share']}%"
        if _pp(c, p, "exec_dir_share") >= 3 else None)),
    ("AI 渗透深化", 1, lambda c, p: (
        f"非技术岗 AI 渗透 {p['nontech_ai_penetration']}%→{c['nontech_ai_penetration']}%"
        if _pp(c, p, "nontech_ai_penetration") >= 8 else None)),
]

def _rel(c, p, f):
    try:
        pv = float(p[f])
        return (float(c[f]) - pv) / pv * 100 if pv else 0.0
    except (ValueError, TypeError, KeyError):
        return 0.0

def _pp(c, p, f):
    try:
        return float(c[f]) - float(p[f])
    except (ValueError, TypeError, KeyError):
        return 0.0

def strategy_radar(summaries, prev, changes):
    """对比上次快照,输出每家公司的显著跳跃(战略走向证据)。"""
    findings = []
    for s in summaries:
        p = prev.get(s["ticker"])
        if not p:
            continue
        items, score = [], 0
        for label, sev, rule in JUMP_RULES:
            desc = rule(s, p)
            if desc:
                items.append((label, sev, desc))
                score += sev
        ch = changes.get(s["ticker"], {})
        if ch.get("new_locations") and len(ch["new_locations"]) >= 3:
            items.append(("新市场扩张", 2, "新地点: " + ", ".join(ch["new_locations"][:6])))
            score += 2
        if ch.get("red_flags"):
            for f in ch["red_flags"]:
                items.append(("红旗", 3, f))
                score += 3
        if items:
            findings.append((score, s, items))
    findings.sort(key=lambda x: -x[0])
    return findings

# ---------- 报告 ----------

def report(summaries, hist, changes, prev_date):
    prev = {}
    prev_dates = sorted({r["date"] for r in hist})
    if prev_dates:
        prev = {r["ticker"]: r for r in hist if r["date"] == prev_dates[-1]}

    def delta(s, field):
        p = prev.get(s["ticker"])
        if not p or p.get(field, "") == "":
            return ""
        try:
            d = float(s[field]) - float(p[field])
        except (ValueError, TypeError):
            return ""
        cls = "up" if d > 0 else ("down" if d < 0 else "")
        return f' <span class="{cls}">{d:+.1f}</span>'

    def bar(v, vmax, color="#4a7cb5"):
        pct = (float(v) / vmax * 100) if vmax else 0
        return (f'<div class="bar"><div style="width:{pct:.0f}%;background:{color}"></div>'
                f'<span>{v}</span></div>')

    dmax = max(float(s["weighted_density"]) for s in summaries) or 1
    main_rows, struct_rows = "", ""
    for theme in dict.fromkeys(s["theme"] for s in summaries):
        group = sorted([s for s in summaries if s["theme"] == theme],
                       key=lambda x: -float(x["weighted_density"]))
        main_rows += f'<tr class="theme"><td colspan="7">{theme}</td></tr>'
        struct_rows += f'<tr class="theme"><td colspan="7">{theme}</td></tr>'
        for s in group:
            direction = f"模型 {s['model_infra_share']}% / 应用 {s['application_share']}%"
            main_rows += f"""<tr><td><b>{s['company']}</b> <small>{s['ticker']}</small></td>
<td>{s['total_postings']}{delta(s,'total_postings')}</td>
<td>{s['ai_title_share']}%{delta(s,'ai_title_share')}</td>
<td>{bar(s['weighted_density'], dmax)}{delta(s,'weighted_density')}</td>
<td>{s['demand_validated_share']}%{delta(s,'demand_validated_share')}</td>
<td>{s['nontech_ai_penetration']}%{delta(s,'nontech_ai_penetration')}</td>
<td><small>{direction}</small></td></tr>"""
            pay = (f"AI ${s['ai_salary_mid']:,} / 非 AI ${s['nonai_salary_mid']:,} / 溢价 {s['ai_pay_premium']}%"
                   if s["ai_pay_premium"] != "" else "样本不足")
            struct_rows += f"""<tr><td><b>{s['company']}</b></td>
<td>{s['exec_dir_share']}%{delta(s,'exec_dir_share')}</td>
<td>{s['junior_share']}%</td>
<td>{s['salary_coverage']}%</td>
<td>{pay}</td>
<td><small>{s['top_vendors'] or '—'}</small></td>
<td><small>{s['top_terms']}</small></td></tr>"""

    findings = strategy_radar(summaries, prev, changes)
    if findings:
        sev_cls = {3: "flag", 2: "warn", 1: "info"}
        jumps = ""
        for score, s, items in findings:
            rows_j = "".join(
                f'<li><span class="{sev_cls[sev]}">{label}</span> {desc}</li>'
                for label, sev, desc in items)
            jumps += f"<h3>{s['company']} <small>{s['ticker']} · {s['theme']}</small></h3><ul>{rows_j}</ul>"
        jump_html = (f"<h2>战略动向雷达(相对 {prev_dates[-1]} 的显著跳跃)</h2>"
                     f"<p class='meta'>按变化强度排序;阈值见 methodology.md。跳跃是研究线索,结论需经财报勾稽。</p>{jumps}")
    else:
        jump_html = ("<h2>战略动向雷达</h2><p class='meta'>本期无超过阈值的显著跳跃。</p>"
                     if prev else
                     "<h2>战略动向雷达</h2><p class='meta'>首次采集为基线,跳跃检测将从第二次运行开始。</p>")

    radar = ""
    for s in summaries:
        ch = changes.get(s["ticker"], {})
        items = []
        if ch.get("red_flags"):
            items += [f'<span class="flag">红旗</span> {f}' for f in ch["red_flags"]]
        if ch.get("removed"):
            items.append(f"下架职位 {ch['removed']} 个")
        if ch.get("new_locations"):
            items.append("新地点: " + ", ".join(ch["new_locations"][:8]))
        if ch.get("new_departments"):
            items.append("新部门: " + ", ".join(ch["new_departments"][:8]))
        if items:
            radar += f"<li><b>{s['company']}</b> — " + ";".join(items) + "</li>"
    radar_html = (f"<h2>变化雷达</h2><ul>{radar}</ul>" if radar else
                  "<h2>变化雷达</h2><p class='meta'>首次采集为基线,消失职位、新地点、新部门将从第二次运行开始检测。</p>"
                  if not prev_date else "<h2>变化雷达</h2><p class='meta'>本期无显著变化。</p>")

    note = f"环比基准:{prev_dates[-1]}" if prev_dates else "首次采集(v2 口径),为基线快照。"
    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>AI 招聘信号报告 {TODAY}</title><style>
body{{font-family:-apple-system,'PingFang SC',sans-serif;max-width:1200px;margin:2em auto;padding:0 1em;color:#222}}
table{{border-collapse:collapse;width:100%;font-size:13px;margin-bottom:2em}}
th,td{{border-bottom:1px solid #ddd;padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#f5f5f5}}
tr.theme td{{background:#eef2f7;font-weight:600;color:#345}}
.bar{{position:relative;background:#eee;border-radius:3px;min-width:110px;height:17px}}
.bar div{{height:100%;border-radius:3px}}
.bar span{{position:absolute;left:6px;top:0;font-size:11px;line-height:17px}}
.up{{color:#c0392b;font-weight:600}} .down{{color:#27ae60;font-weight:600}}
.flag{{background:#c0392b;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px}}
.warn{{background:#d68910;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px}}
.info{{background:#7f8c8d;color:#fff;padding:1px 6px;border-radius:3px;font-size:11px}}
h3{{margin:0.8em 0 0.2em}} h3 small{{color:#888;font-weight:400}}
.meta{{color:#666;font-size:13px}} li{{margin:4px 0;font-size:14px}}
</style></head><body>
<h1>AI 招聘信号报告 <small>{TODAY}</small></h1>
<p class="meta">数据来源:各公司官方招聘 API(Greenhouse/Lever)。{note}
红色=上升,绿色=下降(相对上次采集)。本报告是基本面研究的输入,不构成投资建议。</p>
{jump_html}
<h2>核心信号(按主题分组,组内按技能密度排序)</h2>
<table><tr><th>公司</th><th>在招职位</th><th>AI 头衔%</th><th>加权技能密度</th>
<th>需求验证型%</th><th>非技术岗 AI 渗透</th><th>AI 方向(模型/应用)</th></tr>
{main_rows}</table>
<h2>结构信号(级别 / 薪酬 / 生态)</h2>
<table><tr><th>公司</th><th>高管+总监%</th><th>初级/实习%</th><th>薪酬覆盖率</th>
<th>薪酬中位数与 AI 溢价</th><th>供应商信号</th><th>高频技能词</th></tr>
{struct_rows}</table>
{radar_html}
<p class="meta">指标口径详见 references/methodology.md。加权技能密度 = 每职位命中 AI 技能词的加权和均值
(模型/应用类×3,数据/通用类×2,口号词×1,词条去重);AI 方向 = 命中 AI 内容的职位中涉及模型自建 vs 应用集成的占比;
供应商信号 = JD 中出现的第三方 AI 供应商(反映"在给谁交钱")。</p>
</body></html>"""
    out = ROOT / "reports" / f"report_{TODAY}.html"
    out.write_text(doc)
    print(f"[OK] 报告: {out}")

if __name__ == "__main__":
    main()
