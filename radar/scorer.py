"""打分引擎。

两条轨道用两套质量算法：
  - 产业岗（track: industry）→ 看「公司质量」：融资轮次 + 一线机构背书 + 融资新鲜度
  - 投资岗（track: investment）→ 看「机构质量」：机构 tier

六维加权：岗位匹配 / 公司·机构质量 / 背景契合 / 经验门槛 / 新鲜度 / 城市
所有权重来自画像，此处只做计算。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .core import (
    Profile,
    Watchlist,
    compile_patterns,
    count_hits,
    days_since,
    load_industry,
    parse_date_loose,
)


@dataclass
class Job:
    """一条岗位记录。所有数据源都归一化成这个结构。"""
    title: str
    company: str = ""
    company_id: str = ""
    location: str = ""
    salary: str = ""
    posted: str = ""
    url: str = ""
    jd: str = ""
    source: str = ""
    dept: str = ""

    @classmethod
    def from_dict(cls, d: dict, source: str = "") -> "Job":
        def s(k: str) -> str:
            v = d.get(k)
            return "" if v is None else str(v).strip()
        return cls(
            title=s("title"), company=s("company"), company_id=s("company_id"),
            location=s("location"), salary=s("salary") or s("comp"), posted=s("posted") or s("date"),
            url=s("url"), jd=s("jd"), source=s("source") or source, dept=s("dept"),
        )


@dataclass
class ScoredJob:
    job: Job
    track: str
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)
    hits: dict[str, list[str]] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.job.title, "company": self.job.company, "company_id": self.job.company_id,
            "location": self.job.location, "salary": self.job.salary, "posted": self.job.posted,
            "url": self.job.url, "source": self.job.source, "track": self.track,
            "score": round(self.score, 1),
            "breakdown": {k: round(v, 1) for k, v in self.breakdown.items()},
            "gaps": self.gaps, "notes": self.notes,
            "strength_hits": self.hits.get("strength", []),
        }


# ---------------------------------------------------------------------------
#  硬性筛选
# ---------------------------------------------------------------------------

def _salary_lower_bound_k(salary: str) -> float | None:
    """从「30-60k·18薪」「25-50K」「1.5-3.5万」里取月薪下限（千元）。"""
    if not salary:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", salary)
    val = float(m.group(1)) if m else None
    if val is None:
        m1 = re.search(r"(\d+(?:\.\d+)?)", salary)
        if not m1:
            return None
        val = float(m1.group(1))
    if ("万" in salary) and val < 100:
        return val * 10
    return val


def hard_filter(job: Job, profile: Profile) -> tuple[bool, str]:
    f = profile.hard_filters

    url_pats = f.get("exclude_url_patterns") or []
    if url_pats and job.url:
        m = compile_patterns(url_pats).search(job.url)
        if m:
            return False, f"命中 URL 排除模式「{m.group(0)}」"

    # 排除词只匹配岗位名 —— 若连 JD 正文一起匹配，正文里一句「欢迎应届生」会误杀整个岗位
    if f.get("exclude_keywords"):
        m = compile_patterns(f["exclude_keywords"], word_boundary=True).search(job.title)
        if m:
            return False, f"命中排除词「{m.group(0)}」"

    if f.get("exclude_companies"):
        m = compile_patterns(f["exclude_companies"]).search(job.company)
        if m:
            return False, "公司在排除名单内"

    cities = f.get("cities") or []
    if cities and f.get("city_strict"):
        if not any(c in job.location for c in cities):
            return False, f"城市不符（{job.location or '未知'}）"

    min_k = f.get("min_salary_k") or 0
    if min_k:
        lo = _salary_lower_bound_k(job.salary)
        if lo is not None and lo < min_k:
            return False, f"薪资下限 {lo:.0f}k 低于阈值 {min_k}k"

    return True, ""


# ---------------------------------------------------------------------------
#  各维度打分
# ---------------------------------------------------------------------------

def _title_score(title: str, keywords: dict, blocklist: list[str]) -> tuple[float, bool, list[str]]:
    """岗位匹配度。命中 strong → 100；仅命中 normal → 68。"""
    if blocklist and compile_patterns(blocklist, word_boundary=True).search(title):
        return 0.0, True, []

    s_hits = count_hits(title, compile_patterns(keywords.get("strong") or [], word_boundary=True))
    n_hits = count_hits(title, compile_patterns(keywords.get("normal") or [], word_boundary=True))
    if s_hits:
        return 100.0, False, s_hits + n_hits
    if n_hits:
        return 68.0, False, n_hits
    return 0.0, False, []


def _freshness_score(posted: str) -> float:
    d = days_since(parse_date_loose(posted))
    if d is None:
        return 50.0
    if d < 0:
        return 100.0
    for lo, hi, sc in ((0, 7, 100), (7, 30, 90), (30, 90, 70), (90, 180, 50), (180, 365, 30)):
        if lo <= d < hi:
            return float(sc)
    return 15.0


def _city_score(location: str, profile: Profile) -> float:
    """目标城市 100；「目标城市 + 其他城市」多地点 78；非目标城市 20。

    多地点必须单独处理：很多公司（尤其大厂）在京沪同时挂同一个岗位，
    若简单按「含目标城市」给满分，外地岗位会挤满榜单。
    """
    cities = profile.hard_filters.get("cities") or []
    if not cities:
        return 100.0
    loc = location or ""
    primary = cities[0]
    if primary not in loc:
        return 20.0
    others = [c for c in cities[1:]] + ["上海", "北京", "深圳", "杭州", "广州", "苏州", "成都", "南京"]
    if any(c != primary and c in loc for c in others):
        return 78.0
    return 100.0


def _body_fit(jd: str, profile: Profile) -> tuple[float, list[str], list[str]]:
    strengths = profile.merged_strength_words()
    gaps = profile.merged_gap_words()

    text = jd or ""
    shits = count_hits(text, compile_patterns(strengths, word_boundary=True))
    ghits = count_hits(text, compile_patterns(gaps, word_boundary=True))

    # 封顶门槛别定太低，否则几乎所有 JD 都拿满分，这一维就没区分度了
    base = min(len(shits) / 15.0, 1.0) * 100.0
    return max(0.0, base - 15.0 * len(ghits)), shits, ghits


_YEARS_ABOVE = re.compile(r"(\d{1,2})\s*年(?:及)?以上")
_YEARS_RANGE = re.compile(r"(\d{1,2})\s*[-~至]\s*(\d{1,2})\s*年")
_YEARS_EXP = re.compile(r"(\d{1,2})\s*年(?:以上)?(?:的)?(?:工作)?经[验历]")


def _min_required_years(text: str) -> int | None:
    years: list[int] = []
    for pat in (_YEARS_ABOVE, _YEARS_RANGE, _YEARS_EXP):
        for m in pat.finditer(text):
            try:
                years.append(int(m.group(1)))
            except (TypeError, ValueError):
                continue
    years = [y for y in years if 0 < y <= 25]
    return min(years) if years else None


def _experience_score(jd: str, title: str, profile: Profile) -> tuple[float, str]:
    """经验门槛分：要求越低分越高。

    这一维是「愿意做技术岗，但优先低门槛的」这类诉求的落点 ——
    不靠黑名单拦截岗位，而是让门槛低的自然浮上来。
    """
    g = profile.gates
    text = f"{title} {jd}"

    if g.get("hard_gate_keywords"):
        m = compile_patterns(g["hard_gate_keywords"], word_boundary=True).search(text)
        if m:
            return float(g.get("hard_gate_score", 20)), f"硬门槛：{m.group(0)}"

    years = _min_required_years(text)
    if years is None:
        return float(g.get("unspecified", 72)), ""

    for band in g.get("bands", []):
        if years <= int(band.get("max_years", 99)):
            return float(band.get("score", 50)), f"要求 {years} 年以上"
    return 50.0, f"要求 {years} 年以上"


def _funding_recency(cfg: dict, fdate: str | None) -> float:
    d = days_since(parse_date_loose(fdate))
    if d is None:
        return 40.0
    for band in cfg.get("funding_recency", []):
        if d <= int(band.get("max_days", 99999)):
            return float(band.get("score", 40))
    return 10.0


def _company_quality(company: dict | None, profile: Profile) -> tuple[float, list[str], list[str]]:
    """产业岗：公司质量。一线机构背书 65% + 融资新鲜度 35%。"""
    if not company:
        return 45.0, [], []
    if not company.get("tier1_backers"):
        # 名单外的公司在用户自建池里 —— 用融资轮次粗判
        pass

    q = profile.raw.get("quality") or {}
    t1_list, t2_list = profile.merged_tier_investors()
    backers = company.get("tier1_backers") or []
    funding = company.get("funding") or {}
    all_inv = list(funding.get("lead") or []) + list(funding.get("follow") or [])

    t1 = compile_patterns(t1_list)
    t2 = compile_patterns(t2_list)
    t1_hits = [b for b in backers if t1.search(str(b))] or count_hits(" ".join(map(str, all_inv)), t1)
    t2_hits = count_hits(" ".join(map(str, all_inv)), t2)

    n = len(set(t1_hits))
    base = 95.0 if n >= 3 else 85.0 if n == 2 else 70.0 if n == 1 else 55.0 if t2_hits else 40.0
    rec = _funding_recency(q, funding.get("date"))
    score = 0.65 * base + 0.35 * rec

    if company.get("priority") == 1:
        score += 5.0
    notes: list[str] = []
    if company.get("confidence") == "unverified":
        score -= 8.0
        notes.append("该公司融资数据未经核实")
    return min(100.0, score), sorted(set(map(str, t1_hits))), notes


def _firm_quality(inst: dict | None) -> tuple[float, list[str]]:
    """投资岗：机构质量。tier 1 市场化机构得分最高。"""
    if not inst:
        return 45.0, ["该机构不在机构池中，无法评估质量"]
    tier = int(inst.get("tier") or 3)
    score = {1: 100.0, 2: 72.0, 3: 52.0}.get(tier, 45.0)
    if inst.get("deals"):
        score += 4.0
    if inst.get("hiring"):
        score += 6.0
    notes = []
    if tier == 1:
        notes.append("市场化头部机构")
    elif tier == 2:
        notes.append("产业 CVC / 国资平台")
    return min(100.0, score), notes


def _fuzzy_company(name: str, wl: Watchlist) -> dict | None:
    if not name:
        return None
    for c in wl.active():
        for key in ("name", "full_name"):
            v = c.get(key)
            if v and (v in name or name in v):
                return c
    return None


def _fuzzy_inst(name: str, wl: Watchlist) -> dict | None:
    if not name:
        return None
    for i in wl.active_firms():
        v = i.get("name")
        if v and (v in name or name in v):
            return i
    return None


# ---------------------------------------------------------------------------
#  主入口
# ---------------------------------------------------------------------------

def score_job(job: Job, track: str, profile: Profile, watchlist: Watchlist) -> ScoredJob | None:
    ok, _ = hard_filter(job, profile)
    if not ok:
        return None

    cfg = profile.merged_track(track)
    w = cfg["weights"]

    t_score, blocked, title_hits = _title_score(job.title, cfg["title_keywords"], cfg["title_blocklist"])
    if blocked or t_score == 0.0:
        return None

    fresh = _freshness_score(job.posted)
    city = _city_score(job.location, profile)
    body, shits, ghits = _body_fit(job.jd, profile)
    exp, exp_note = _experience_score(job.jd, job.title, profile)

    notes: list[str] = []
    if exp_note:
        notes.append(exp_note)

    mode = (profile.raw.get("quality_mode") or {}).get(track)
    if mode is None:
        mode = "firm" if track == "investment" else "company"

    if mode == "firm":
        inst = _fuzzy_inst(job.company, watchlist)
        quality, qnotes = _firm_quality(inst)
        notes.extend(qnotes)
        if inst:
            notes.append(f"{inst.get('type', '')} · tier {inst.get('tier', '?')}")
    elif mode == "company":
        company = watchlist.by_id(job.company_id) or _fuzzy_company(job.company, watchlist)
        quality, backers, qnotes = _company_quality(company, profile)
        notes.extend(qnotes)
        if backers:
            notes.append("一线机构：" + "、".join(backers[:3]))
        if company:
            f = company.get("funding") or {}
            if f.get("amount"):
                notes.append(f"{f.get('latest_round', '融资')} {f.get('amount')}")
    else:
        quality = 60.0

    total = (
        t_score * w.get("title_match", 15)
        + quality * w.get("quality", 22)
        + body * w.get("body_fit", 20)
        + exp * w.get("experience", 28)
        + fresh * w.get("freshness", 5)
        + city * w.get("city", 10)
    ) / 100.0

    return ScoredJob(
        job=job, track=track, score=total,
        breakdown={
            "岗位匹配": t_score, "公司/机构质量": quality, "背景契合": body,
            "经验门槛": exp, "新鲜度": fresh, "城市": city,
        },
        hits={"title": title_hits, "strength": shits, "gap": ghits},
        gaps=ghits, notes=notes,
    )


def score_all(jobs: list[Job], profile: Profile, watchlist: Watchlist) -> tuple[list[ScoredJob], int]:
    """同一岗位可能在多条轨道上都命中（例如「投资经理」既是产业岗也是投资岗）。"""
    scored: list[ScoredJob] = []
    rejected = 0
    tracks = profile.enabled_tracks()

    for job in jobs:
        ok, _reason = hard_filter(job, profile)
        if not ok:
            rejected += 1
            continue
        any_hit = False
        for track in tracks:
            s = score_job(job, track, profile, watchlist)
            if s:
                scored.append(s)
                any_hit = True
        if not any_hit:
            rejected += 1

    scored.sort(key=lambda s: -s.score)
    return scored, rejected
