"""报告生成：Markdown（归档/可读）+ HTML（深色速览）+ JSON（机器对比）。"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from .core import DATA_DIR, REPORT_DIR, Profile, Watchlist
from .scorer import ScoredJob

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2128;--line:#30363d;--fg:#e6edf3;
--dim:#8b949e;--accent:#58a6ff;--ok:#3fb950;--warn:#d29922;--hot:#ff7b72;}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;background:var(--bg);color:var(--fg);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:19px;margin:34px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:15px;margin:0;line-height:1.5}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.stat .n{font-size:22px;font-weight:600;color:var(--accent)}
.stat .l{font-size:12px;color:var(--dim);margin-top:2px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:10px}
.card.alert{border-left:3px solid var(--hot)}
.card.new{border-left:3px solid var(--ok)}
.card.gap{border-left:3px solid var(--warn)}
.row{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.score{font-size:17px;font-weight:600;color:var(--ok);white-space:nowrap}
.score.hi{color:var(--hot)}
.meta{color:var(--dim);font-size:12.5px;margin-top:6px;display:flex;gap:14px;flex-wrap:wrap}
.tags{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
.tag{font-size:11.5px;padding:2px 8px;border-radius:20px;background:var(--panel2);
border:1px solid var(--line);color:var(--dim)}
.tag.good{color:var(--ok);border-color:#238636}
.tag.gap{color:var(--warn);border-color:#9e6a03}
.bar{height:5px;background:var(--panel2);border-radius:3px;margin-top:9px;overflow:hidden;display:flex}
.bar i{display:block;height:100%}
b.new{color:var(--ok);font-size:11px;border:1px solid var(--ok);border-radius:3px;padding:0 5px;margin-left:6px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.empty{color:var(--dim);font-style:italic;padding:14px 0}
.legend{font-size:12px;color:var(--dim);margin-top:10px}
.legend span{margin-right:16px}
footer{color:var(--dim);font-size:12px;margin-top:30px;text-align:center}
"""

DEFAULT_WEIGHTS = {"岗位匹配": 15, "公司/机构质量": 22, "背景契合": 20, "经验门槛": 28, "新鲜度": 5, "城市": 10}


def _weights_for(track: str, profile: Profile) -> dict[str, int]:
    cfg = profile.merged_track(track)
    w = cfg.get("weights") or {}
    return {
        "岗位匹配": int(w.get("title_match", 15)),
        "公司/机构质量": int(w.get("quality", 22)),
        "背景契合": int(w.get("body_fit", 20)),
        "经验门槛": int(w.get("experience", 28)),
        "新鲜度": int(w.get("freshness", 5)),
        "城市": int(w.get("city", 10)),
    }


def _bar(breakdown: dict, weights: dict[str, int]) -> str:
    colors = ["#58a6ff", "#3fb950", "#a371f7", "#f778ba", "#d29922", "#8b949e"]
    segs = []
    for (k, v), c in zip(breakdown.items(), colors):
        segs.append(f'<i style="width:{v * weights.get(k, 15) / 100:.2f}%;background:{c}" title="{k} {v:.0f}"></i>')
    return f'<div class="bar">{"".join(segs)}</div>'


def _card(s: ScoredJob, alert_score: float, new_urls: set[str], weights: dict[str, int]) -> str:
    j = s.job
    is_new = bool(j.url) and j.url in new_urls
    cls = "card alert" if s.score >= alert_score else ("card new" if is_new else "card")
    if s.gaps and s.score < alert_score and cls == "card":
        cls = "card gap"

    tags = []
    for n in s.notes[:2]:
        tags.append(f'<span class="tag good">{html.escape(str(n))}</span>')
    for sh in (s.hits.get("strength") or [])[:4]:
        tags.append(f'<span class="tag">{html.escape(str(sh))}</span>')
    for g in s.gaps[:3]:
        tags.append(f'<span class="tag gap">缺：{html.escape(str(g))}</span>')

    title = html.escape(j.title)
    if j.url:
        title = f'<a href="{html.escape(j.url)}" target="_blank" rel="noopener">{title}</a>'
    badge = '<b class="new">NEW</b>' if is_new else ""

    meta = [f"🏢 {html.escape(j.company)}"]
    if j.location:
        meta.append(f"📍 {html.escape(j.location)}")
    if j.salary:
        meta.append(f"💰 {html.escape(j.salary)}")
    if j.posted:
        meta.append(f"🗓 {html.escape(str(j.posted))[:10]}")
    if j.source:
        meta.append(f"· {html.escape(j.source)}")

    gap_line = ""
    if s.gaps:
        gap_line = f'<div class="meta" style="color:#d29922">⚠️ 缺口：{"、".join(html.escape(str(x)) for x in s.gaps)}</div>'

    # 预先拼好，不要塞进 f-string —— Python 3.11 及更早不允许 f-string 表达式里出现反斜杠
    tags_html = '<div class="tags">' + "".join(tags) + "</div>" if tags else ""
    score_cls = " hi" if s.score >= alert_score else ""

    return (
        f'<div class="{cls}">'
        f'<div class="row"><h3>{title}{badge}</h3><div class="score{score_cls}">{s.score:.0f}</div></div>'
        f'<div class="meta">{" ".join(meta)}</div>'
        f'{_bar(s.breakdown, weights)}'
        f'{tags_html}'
        f'{gap_line}</div>'
    )


def build_html(scored, profile, watchlist, new_urls, source_status, notes) -> str:
    today = date.today().isoformat()
    out = profile.output
    alert = float(out.get("alert_score", 80))
    top_n = int(out.get("top_n_per_track", 25))

    by_track = defaultdict(list)
    for s in scored:
        by_track[s.track].append(s)

    st = watchlist.stats()
    ind_names = "、".join(profile.industries) or "未指定"
    parts = [
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>求职雷达 · {today}</title><style>{CSS}</style></head><body><div class="wrap">',
        f'<h1>🎯 求职雷达日报</h1>',
        f'<div class="sub">{today} · 行业：{html.escape(ind_names)} · 公司池 {st["total"]} 家'
        f'（可自动抓取 {st["with_portal"]} 家）· 城市：{"、".join(profile.hard_filters.get("cities") or ["不限"])}</div>',
    ]

    hot = [s for s in scored if s.score >= alert]
    parts.append('<div class="stats">')
    for n, l in ((len(scored), "本次评分岗位"), (len(hot), f"高优先级（≥{alert:.0f}）"),
                 (len(new_urls), "较上次新增"), (st["with_portal"], "可自动抓取门户"),
                 (st["total"], "公司池规模")):
        parts.append(f'<div class="stat"><div class="n">{n}</div><div class="l">{l}</div></div>')
    parts.append("</div>")

    if hot:
        parts.append("<h2>🔥 高优先级岗位</h2>")
        for s in sorted(hot, key=lambda x: -x.score)[:12]:
            parts.append(_card(s, alert, new_urls, _weights_for(s.track, profile)))

    for track in profile.enabled_tracks():
        cfg = profile.merged_track(track)
        items = by_track.get(track, [])[:top_n]
        w = _weights_for(track, profile)
        parts.append(f'<h2>{cfg.get("emoji", "")} {html.escape(cfg.get("label", track))} · Top {len(items)}</h2>')
        if not items:
            parts.append('<div class="empty">本轮没有匹配的岗位。检查下方「数据源状态」看是不是抓取出了问题。</div>')
        for s in items:
            parts.append(_card(s, alert, new_urls, w))

    if notes:
        parts.append("<h2>📡 动态</h2>")
        for n in notes:
            parts.append(f'<div class="card"><div class="meta">{html.escape(n)}</div></div>')

    parts.append('<h2>🔧 数据源状态</h2><div class="card">')
    for k, v in source_status.items():
        parts.append(f'<div class="meta">· <b>{html.escape(k)}</b> — {html.escape(v)}</div>')
    parts.append("</div>")

    parts.append(
        '<div class="legend"><span>📊 评分条：岗位匹配 · 公司/机构质量 · 背景契合 · 经验门槛 · 新鲜度 · 城市</span>'
        f'<span>分值 0-100，≥{alert:.0f} 标红</span></div>'
    )
    parts.append('<footer>由 cn-job-radar 生成 · 数据来自公开渠道，仅供参考</footer>')
    parts.append("</div></body></html>")
    return "".join(parts)


def build_markdown(scored, profile, watchlist, new_urls, source_status, notes) -> str:
    today = date.today().isoformat()
    top_n = int(profile.output.get("top_n_per_track", 25))
    by_track = defaultdict(list)
    for s in scored:
        by_track[s.track].append(s)

    st = watchlist.stats()
    L = [
        f"# 求职雷达日报 · {today}", "",
        f"- 行业：{'、'.join(profile.industries) or '未指定'}",
        f"- 城市：{'、'.join(profile.hard_filters.get('cities') or ['不限'])}",
        f"- 公司池：{st['total']} 家（可自动抓取 {st['with_portal']} 家）",
        f"- 本次评分：{len(scored)} 条，其中新增 {len(new_urls)} 条", "",
    ]

    for track in profile.enabled_tracks():
        cfg = profile.merged_track(track)
        items = by_track.get(track, [])[:top_n]
        L += [f"## {cfg.get('emoji', '')} {cfg.get('label', track)}（{len(items)} 条）", ""]
        if not items:
            L += ["_本轮无匹配岗位。_", ""]
            continue
        L += ["| 分 | 岗位 | 公司 | 地点 | 薪资 | 发布 | 缺口 |", "|---|---|---|---|---|---|---|"]
        for s in items:
            j = s.job
            flag = " 🆕" if j.url in new_urls else ""
            link = f"[{j.title}]({j.url})" if j.url else j.title
            L.append(
                f"| {s.score:.0f}{flag} | {link} | {j.company} | {j.location or '—'} | "
                f"{j.salary or '—'} | {str(j.posted)[:10] or '—'} | {'、'.join(s.gaps[:2]) or '—'} |"
            )
        L.append("")

    if notes:
        L += ["## 📡 动态", ""] + [f"- {n}" for n in notes] + [""]

    L += ["## 🔧 数据源状态", ""] + [f"- **{k}** — {v}" for k, v in source_status.items()] + [""]
    return "\n".join(L)


def write_report(scored, profile, watchlist, source_status, notes) -> dict[str, Path]:
    today = date.today().isoformat()
    new_urls = _diff_with_previous(scored)

    md = build_markdown(scored, profile, watchlist, new_urls, source_status, notes)
    doc = build_html(scored, profile, watchlist, new_urls, source_status, notes)

    paths = {
        "md": REPORT_DIR / f"{today}.md",
        "html": REPORT_DIR / f"{today}.html",
        "json": DATA_DIR / "scored" / f"{today}.json",
    }
    paths["md"].write_text(md, encoding="utf-8")
    paths["html"].write_text(doc, encoding="utf-8")
    paths["json"].write_text(json.dumps({
        "date": today,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "industries": profile.industries,
        "source_status": source_status,
        "notes": notes,
        "jobs": [s.to_dict() for s in scored],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    _prune(int(profile.output.get("keep_reports_days", 90)))
    return paths


def _diff_with_previous(scored: list[ScoredJob]) -> set[str]:
    """与**上一次**的结果对比，找出本次新增的岗位。

    要排除今天的存档 —— 否则第二次运行时拿今天和自己比，全部会被判成「非新增」。
    """
    today = date.today().isoformat()
    files = [f for f in sorted((DATA_DIR / "scored").glob("*.json")) if f.stem != today]
    if not files:
        return {s.job.url for s in scored if s.job.url}
    try:
        data = json.loads(files[-1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    old = {j.get("url") for j in data.get("jobs", []) if j.get("url")}
    return {s.job.url for s in scored if s.job.url and s.job.url not in old}


def _prune(keep_days: int) -> None:
    if keep_days <= 0:
        return
    cutoff = date.today().toordinal() - keep_days
    for p in REPORT_DIR.glob("*.*"):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d.toordinal() < cutoff:
            p.unlink(missing_ok=True)
