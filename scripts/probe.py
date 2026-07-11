#!/usr/bin/env python3
"""探测一家公司用的招聘系统(ATS)和 board token,用于向 config.json 添加新公司。

用法: python3 scripts/probe.py <猜测词1> [猜测词2] ...
例如: python3 scripts/probe.py snowflake snowflakecomputing
对每个猜测词依次探测 Greenhouse / Lever / Ashby 三个公开接口,输出可用的组合。
"""
import sys, urllib.request

ENDPOINTS = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{t}/jobs"),
    ("lever", "https://api.lever.co/v0/postings/{t}?mode=json"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{t}"),
]

def status(url):
    req = urllib.request.Request(url, headers={"User-Agent": "hiring-signal-tracker/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1

if len(sys.argv) < 2:
    sys.exit(__doc__)
found = False
for token in sys.argv[1:]:
    for ats, tpl in ENDPOINTS:
        code = status(tpl.format(t=token))
        if code == 200:
            print(f'[FOUND] {{"ats": "{ats}", "token": "{token}"}}')
            found = True
if not found:
    print("[MISS] 所有猜测均不可用。该公司可能使用 Workday 或自建系统,当前版本不支持。")
