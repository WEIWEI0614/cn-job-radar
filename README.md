<div align="center">

# 🎯 cn-job-radar

**Job radar for the Chinese job market.**
Watches **companies' own career portals** — not job boards — then scores every opening against your profile.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Dependencies](https://img.shields.io/badge/runtime%20deps-1-brightgreen)](pyproject.toml)
[![Local first](https://img.shields.io/badge/data-stays%20local-success)](#privacy)

[English](README.md) · [简体中文](README.zh-CN.md)

</div>

---

## Why another job tool

Every open-source job-search project on GitHub aims at **Western job boards** — LinkedIn, Indeed, Greenhouse, Lever, Workday. None of them know about **Feishu Hire (飞书招聘)**, **Moka**, or **Beisen (北森)** — the applicant-tracking systems that most Chinese tech companies actually use.

`cn-job-radar` takes the other route:

| | JobSpy / career-ops / AIHawk | **cn-job-radar** |
|---|---|---|
| Where jobs come from | LinkedIn, Indeed, aggregators | **Companies' own ATS portals** (Feishu Hire · Moka · Beisen) |
| Login required | Often | **Never** |
| Job board anti-bot | Constant cat-and-mouse | **Not applicable** — these are public APIs |
| Fits your profile | LLM scoring on a resume | **Six-dimension deterministic scoring you can tune** |
| Data | Leaves your machine | **Stays local** |
| Works for | Western markets | **China (and any ATS-using company worldwide)** |

Reading a company's own portal means you see the posting **the day it goes up**, before aggregators index it — and you never fight CAPTCHAs.

---

## Quick start

```bash
pip install cn-job-radar      # or: pip install -e .

radar init                    # interactive: pick industries, cities, tracks
radar setup                   # one-time: installs the scraping engine
radar run                     # fetch → score → daily report
radar ui                      # optional: open the web UI at 127.0.0.1:8787
```

Reports land in `~/.cn-job-radar/reports/`.

> Just want to look around first? `radar init --industry ai --cities 北京 --yes` skips all prompts.

---

## How it works

```
                 ┌──────────────────────────────────────────┐
                 │  your watchlist (companies.yaml)         │
                 │  "the system only sees what you list"    │
                 └────────────────┬─────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        ▼                         ▼                         ▼
  Feishu Hire                Moka                    Beisen
  xxx.jobs.feishu.cn   app.mokahr.com/apply/…    xxx.zhiye.com
        └─────────────────────────┼─────────────────────────┘
                                  ▼
                    unified schema (title / location / salary / JD / url)
                                  ▼
                    ┌──────────────────────────┐
                    │   six-dimension scoring  │
                    └──────────────────────────┘
                                  ▼
              Markdown + HTML report · JSON · web UI
```

### The watchlist is everything

The tool only sees companies **you** list. That's deliberate: instead of scraping the whole internet and drowning in noise, you keep a high-quality list. **Add one company = watch one more, forever.**

Adding a company is one YAML block — no code:

```yaml
- id: galbot
  name: 银河通用
  city: 北京
  tracks: [人形机器人]
  funding: {latest_round: 战略轮, amount: 25亿元, date: "2026-03-02", lead: [国家人工智能产业投资基金]}
  tier1_backers: [美团龙珠, 经纬创投, 蓝驰创投]
  portal: {type: feishu, arg: "owm6ymi5v9b.jobs.feishu.cn/870797"}
```

### Six-dimension scoring

```
Job match        15  — hits a "strong" keyword → 100; only "normal" → 68
Company / firm   22  — industry-track: backers + funding recency · investment-track: firm tier
Profile fit      20  — how many of your strength keywords the JD contains (caps at 15), minus gaps
Experience gate  28  — ★ highest weight: extracts "N+ years" from the JD, lower bar → higher score
Freshness         5  — posted within 7 days → 100 · over a year → 15
City             10  — target city 100 · "target + other" 78 · elsewhere 20
```

**Why "experience gate" carries the most weight.** Most job tools filter for *senior* candidates. Many people — career switchers, returning parents, new grads — will take *any* technical role, they just can't clear a "5+ years" bar. Instead of a blacklist, `cn-job-radar` reads the actual years-of-experience requirement out of the JD and **lets the low-bar roles float to the top on their own**. A PhD-required role sinks; a "1+ year, delivery engineer" role rises. One mechanism, three wins: nothing you'd accept gets filtered out, senior roles stop crowding the list, and you never maintain a blocklist.

### Two tracks, two algorithms

| | Industry track | Investment track |
|---|---|---|
| Quality is measured by | the **company**: funding round, amount, tier-1 backers | the **firm**: tier, AUM, deals in the sector |
| Typical sources | company career portals | BOSS Zhipin / Liepin (not on company portals) |

Tier-1 market-driven VCs score above state-owned platforms — a deliberate, editable judgement about which environments reward research-and-conviction over relationships. Change the `tier` numbers if you disagree.

---

## Built-in industry packs

Pick with `radar init`, or combine several.

| id | 行业 / Industry |
|---|---|
| `ai` | 人工智能 · 大模型 |
| `robotics` | 机器人 · 具身智能 |
| `semiconductor` | 半导体 · 芯片 |
| `biotech` | 生物医药 · 医疗健康 |
| `newenergy` | 新能源 · 储能 · 电池 |
| `autotech` | 智能汽车 · 自动驾驶 |
| `finance` | 金融 · 投资 · 量化 |
| `general` | 通用（任意行业） |

Each pack ships job keywords, the active investors in that sector, and industry-detection signals. Override any of them by dropping a same-named file into `~/.cn-job-radar/config/industries/`.

---

## Commands

```bash
radar init [--industry ai,robotics] [--cities 北京 上海] [--yes]
radar setup                  # install the scraping engine
radar run [--no-fetch] [--track industry] [--ingest FILE]
radar stats                  # watchlist status + which companies lack a portal
radar discover [--slugs a,b] # probe for ATS portals
radar ui [--port 8787]       # local web UI
radar industries             # list industry packs
```

### Feeding in jobs from anywhere

Company portals don't cover everything. Anything you find elsewhere — BOSS Zhipin, Liepin, a WeChat post — drop into `data/raw/*.json` and it gets scored with everything else:

```json
[{"title": "投资经理", "company": "君联资本", "location": "北京-海淀",
  "salary": "30-60k·18薪", "posted": "2026-10-14",
  "url": "https://…", "source": "猎聘", "jd": "岗位职责…任职要求…"}]
```

This is also how you pair it with an AI agent: let the agent do the browsing, and let `cn-job-radar` do the scoring.

---

## Privacy

Everything runs locally. Jobs fetch straight from public career-portal APIs; your profile, watchlist, and results never leave your machine. No account, no telemetry, no upload.

The tool reads **only public job postings**. It does not touch résumés, does not log in anywhere, and stops when it meets a CAPTCHA or a login wall.

---

## FAQ

**Do I need an API key?** No.

**Does it work outside China?** The scoring, watchlist, and web UI are market-agnostic, and the engine supports 24 international ATS platforms (Greenhouse, Lever, Ashby, Workday…) on top of the three Chinese ones. Only the built-in industry packs lean China-first.

**A portal returned zero jobs — is it broken?** Not necessarily; the company may genuinely be paused. `cn-job-radar` distinguishes "no openings" from "failed to fetch" and alerts you when that status *changes*.

**Why is salary empty for some jobs?** Feishu Hire doesn't publish salary in its API. Not a bug.

**How do I add a company that has no portal?** Run `radar discover`, or find the portal link on the company's own careers page and add it to `portals.yaml`. Chinese ATS portals follow patterns — Beisen is `<english-name>.zhiye.com`; Feishu Hire uses random slugs you have to look up. BOSS Zhipin company pages return anti-bot shells when fetched, so they don't count as a verified portal.

---

## Contributing

The most valuable contribution is **new companies and their portals** — especially Chinese companies using Feishu Hire / Moka / Beisen. Open a PR against `radar/industries/*.yaml` (for keywords and investors) or share a `portals.yaml` snippet.

Also welcome: new industry packs, locale-specific scoring tweaks, and bug reports from real job hunts.

---

## Credits

Scraping is powered by [Hiring-Radar](https://github.com/simonlin1212/Hiring-Radar) (MIT), fetched on demand by `radar setup` rather than vendored.

## License

MIT — see [LICENSE](LICENSE).
