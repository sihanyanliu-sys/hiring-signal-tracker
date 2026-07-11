# hiring-signal-tracker

把上市科技公司的**官方招聘数据**变成可量化、可追溯的 **AI 投入信号时间序列**,
作为基本面(价值投资)研究的领先证据。它不是选股机器,而是一台领先财报
1~2 个季度的"企业行为记录仪":市场上多数人看公司**说了什么**,它看公司
**做了什么**——批准 headcount 是真金白银的支出承诺。

> 输出仅为研究输入,不构成投资建议。

## 它做什么

每次运行,自动完成:

1. **采集**:调用观察池内各公司官方招聘系统(Greenhouse / Lever / Ashby)的
   公开 JSON API,拿到全部在招职位与 JD 全文(默认池 27 家美股公司,约 5,500 个职位);
2. **打分**:每份 JD 过三层加权 AI 技能词典(具体技术栈 ×3 / 通用词 ×2 / 口号词 ×1),
   并自动剔除公司模板文案(出现在 ≥90% JD 的词条是市场部文案,不是技能需求);
3. **分类**:岗位按价值链角色(需求验证型 / 研究型 / 产品工程 / 非技术)与
   级别(高管 / 总监 / 资深 / 初级)分桶,并提取 JD 公示的薪酬带;
4. **差分**:与历史对比,检测消失职位(红旗)、新地点、新部门;
5. **战略动向雷达**:对比上次快照做跳跃检测(收缩预警 / 扩张加速 / 需求验证跃升 /
   转向模型自建 / 高管招聘潮等十类规则,阈值见 methodology.md 第 5 节),
   按变化强度排序,直接回答"这家公司战略上在往哪走";
6. **累积与报告**:追加公司级时间序列 `data/history.csv`,生成单文件 HTML 报告
   (按主题分组、含环比变化),双击即可打开、可直接转发。

## 快速开始

要求:Python 3.8+(仅标准库,无需 pip install)。

```bash
python3 scripts/pipeline.py          # 采集一次,约 1~3 分钟
open reports/report_$(date +%F).html # 查看报告
```

## 每周自动采集

macOS 用 launchd(错过时点会在唤醒后补跑),模板见
`scripts/weekly.sh` + `~/Library/LaunchAgents/com.hiring-signal-tracker.weekly.plist`
(每周一 09:00 运行:采集 → git 备份 → 系统通知,日志在 `data/weekly.log`)。
加载:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hiring-signal-tracker.weekly.plist
```

Linux 用 cron:`0 9 * * 1 cd /path/to/hiring-signal-tracker && python3 scripts/pipeline.py`

数据从第二次运行起产生环比与红旗检测;积累一个季度后可做财报勾稽,两个季度后
可做信号有效性检验(见 `references/methodology.md` 第 6 节)。

## 修改股票池

1. 探测新公司用的招聘系统:

   ```bash
   python3 scripts/probe.py snowflake snowflakecomputing
   ```

   猜测词可以是公司短名、产品名、去空格名(例:HubSpot 的真实 token 是
   `hubspotjobs`)。命中会输出可直接使用的 `ats`/`token` 组合。

2. 在 `config.json` 的 `companies` 数组增删条目(字段:name / ticker / ats /
   token / theme;theme 用于报告中的同业分组,请把同赛道公司放同一 theme)。

3. 跑一次采集为新公司建基线。

已知采不到的公司:Snowflake、CrowdStrike、Zscaler、Atlassian、UiPath
(Workday 或自建系统)。刻意不收录 Google/Meta/微软/亚马逊——几千个职位会稀释信号。

## 报告怎么读

三个原则(详见 `references/methodology.md`,这是本项目最重要的文件):

- **同公司自身的时间序列变化才是信号**;跨公司比绝对值噪声很大(JD 写作风格不同),
  只在同 theme 内横比才有参考意义。
- **岗位类型决定信号强度**:需求验证型(FDE/解决方案/销售工程,按客户合同配置)>
  产品工程 > 研究;非技术岗 AI 渗透率上升 = AI 进入业务深水区。
- **反向信号最可靠**:公司会夸大扩张,不会假装收缩。看报告先看"变化雷达"里的红旗。

## 目录结构

```
config.json                股票池 + 词典 + 分类规则(所有策略参数都在这里)
scripts/pipeline.py        采集管线(唯一需要运行的脚本)
scripts/probe.py           新公司 ATS 探测
references/methodology.md  方法论:指标口径、信号分层、局限、勾稽与验证流程
data/jobs_<date>.csv       职位明细快照(每职位打分/分类/薪酬,可下钻核查)
data/history.csv           公司级时间序列(核心资产,请纳入备份)
data/state.json            已知地点/部门集合(新信号检测用)
reports/report_<date>.html 报告
```

## 注意事项

- **词典即口径**:修改 `skill_categories` 后,密度类指标与历史不可比。请先把
  `data/history.csv` 移入 `data/archive/`,重建基线。
- **合规边界**:只调用官方公开无鉴权的招聘 API;不抓取 LinkedIn / Glassdoor /
  Indeed(违反其服务条款);不绕过任何访问限制。
- 词典为英文,适用于英文 JD 市场(美股);A 股/港股需自建词典。
- 供应商信号列偶有误匹配(如加密行业的 "Gemini" 指交易所),反常值请人工复核。

## 作为 Agent Skill 使用

本目录符合 [Agent Skills](https://agentskills.io) 开放标准:`SKILL.md` 是
agent 指令入口。放入 Claude Code 的 `~/.claude/skills/` 后,直接说
"跑一次招聘信号采集"、"把 Snowflake 加进股票池"、"做一次财报勾稽" 即可;
Cursor 等支持该标准的工具需手动引用本目录。
