"""cn-job-radar · 配置与数据模型。

设计原则：所有可调参数都在 YAML 里，代码本身不含业务判断。
新用户通过 `radar init` 生成 profile.yaml，通过行业包获得默认关键词。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
#  路径
# ---------------------------------------------------------------------------
PKG_DIR = Path(__file__).resolve().parent
BUILTIN_INDUSTRIES = PKG_DIR / "industries"

# 用户工作目录：默认 ~/.cn-job-radar，可用环境变量覆盖
HOME = Path(os.environ.get("JOB_RADAR_HOME") or (Path.home() / ".cn-job-radar"))
CONFIG_DIR = HOME / "config"
DATA_DIR = HOME / "data"
REPORT_DIR = HOME / "reports"
CACHE_DIR = DATA_DIR / "cache"
PROFILE_FILE = CONFIG_DIR / "profile.yaml"
COMPANIES_FILE = CONFIG_DIR / "companies.yaml"
PORTALS_FILE = CONFIG_DIR / "portals.yaml"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, REPORT_DIR, CACHE_DIR, DATA_DIR / "raw", DATA_DIR / "scored"):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
#  工具
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=100)


def parse_date_loose(value: Any) -> date | None:
    """把 2026-03-02 / 2026-03 / 2026年3月 都解析成 date。"""
    if isinstance(value, date):
        return value
    if not value:
        return None
    m = re.search(r"(\d{4})\D{0,2}(\d{1,2})?\D{0,2}(\d{1,2})?", str(value))
    if not m:
        return None
    try:
        return date(int(m.group(1)), min(max(int(m.group(2) or 1), 1), 12), min(max(int(m.group(3) or 1), 1), 28))
    except ValueError:
        return None


def days_since(d: date | None) -> int | None:
    return None if d is None else (date.today() - d).days


def compile_patterns(words: list[str] | None, word_boundary: bool = False) -> re.Pattern:
    """把关键词列表编译成一个正则。中文不做词边界，英文关键词做。"""
    parts = []
    for w in words or []:
        w = str(w).strip()
        if not w:
            continue
        esc = re.escape(w)
        if word_boundary and re.fullmatch(r"[A-Za-z0-9+#.\- ]+", w):
            esc = rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])"
        parts.append(esc)
    return re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile(r"(?!)")


def count_hits(text: str, pattern: re.Pattern) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    lowered: set[str] = set()
    for m in pattern.finditer(text):
        tok = m.group(0)
        if tok.lower() not in lowered:
            lowered.add(tok.lower())
            seen.append(tok)
    return seen


# ---------------------------------------------------------------------------
#  行业包
# ---------------------------------------------------------------------------

@dataclass
class Industry:
    """一个行业包：提供岗位关键词、行业判断词、一线机构名单等默认值。"""
    raw: dict

    @property
    def id(self) -> str:
        return self.raw.get("id", "")

    @property
    def name(self) -> str:
        return self.raw.get("name", self.id)

    @property
    def name_en(self) -> str:
        return self.raw.get("name_en", self.id)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


def list_industries() -> list[dict]:
    """列出所有内置行业包（含用户自定义的，放在 CONFIG_DIR/industries 下会覆盖同名内置包）。"""
    found: dict[str, Path] = {}
    for d in (BUILTIN_INDUSTRIES, CONFIG_DIR / "industries"):
        if not d.exists():
            continue
        for p in sorted(d.glob("*.yaml")):
            found[p.stem] = p            # 用户目录后扫描，同名覆盖内置
    out = []
    for stem, path in sorted(found.items()):
        raw = load_yaml(path)
        raw.setdefault("id", stem)
        raw["_path"] = str(path)
        out.append(raw)
    return out


def load_industry(industry_id: str) -> Industry | None:
    for d in (CONFIG_DIR / "industries", BUILTIN_INDUSTRIES):
        p = d / f"{industry_id}.yaml"
        if p.exists():
            raw = load_yaml(p)
            raw.setdefault("id", industry_id)
            return Industry(raw)
    return None


# ---------------------------------------------------------------------------
#  求职画像
# ---------------------------------------------------------------------------

@dataclass
class Profile:
    raw: dict

    @property
    def version(self) -> int:
        return int(self.raw.get("version", 1))

    @property
    def industries(self) -> list[str]:
        """用户关注的行业包 id 列表。"""
        return list(self.raw.get("industries") or [])

    @property
    def hard_filters(self) -> dict:
        return self.raw.get("filters", {})

    @property
    def tracks(self) -> dict:
        return self.raw.get("tracks", {})

    @property
    def candidate(self) -> dict:
        return self.raw.get("candidate", {})

    @property
    def gates(self) -> dict:
        return self.raw.get("experience_gate", {})

    @property
    def output(self) -> dict:
        return self.raw.get("output", {})

    def enabled_tracks(self) -> list[str]:
        return [k for k, v in self.tracks.items() if v.get("enabled", True)]

    def merged_track(self, track: str) -> dict:
        """把画像里的轨道配置与行业包的默认值合并。

        画像里的显式配置优先；行业包只提供画像没写的部分。
        """
        cfg = dict(self.tracks.get(track) or {})
        ind_ids = self.industries
        if not ind_ids:
            ind_ids = [self.raw.get("industry") or "general"]

        strong: list[str] = list(cfg.get("title_keywords", {}).get("strong") or [])
        normal: list[str] = list(cfg.get("title_keywords", {}).get("normal") or [])
        if not strong and not normal:
            for iid in ind_ids:
                ind = load_industry(iid)
                if not ind:
                    continue
                kw = ind.get("job_keywords") or {}
                strong += kw.get("strong") or []
                normal += kw.get("normal") or []

        weights = dict(cfg.get("weights") or {})
        defaults = {"title_match": 15, "quality": 22, "body_fit": 20, "experience": 28, "freshness": 5, "city": 10}
        for k, v in defaults.items():
            weights.setdefault(k, v)

        return {
            "label": cfg.get("label") or track,
            "emoji": cfg.get("emoji", ""),
            "title_keywords": {"strong": _dedupe(strong), "normal": _dedupe(normal)},
            "title_blocklist": cfg.get("title_blocklist") or [],
            "weights": weights,
        }

    def merged_gap_words(self) -> list[str]:
        words = list(self.candidate.get("gap_keywords") or [])
        for iid in self.industries:
            ind = load_industry(iid)
            if ind:
                words += ind.get("gap_keywords") or []
        return _dedupe(words)

    def merged_strength_words(self) -> list[str]:
        words = list(self.candidate.get("strength_keywords") or [])
        for iid in self.industries:
            ind = load_industry(iid)
            if ind:
                words += ind.get("strength_keywords") or []
        return _dedupe(words)

    def merged_tier_investors(self) -> tuple[list[str], list[str]]:
        """一线 / 二线机构名单。用户可覆盖，默认来自行业包并集。"""
        q = self.raw.get("quality") or {}
        t1 = list(q.get("tier1_investors") or [])
        t2 = list(q.get("tier2_investors") or [])
        if not t1:
            for iid in self.industries:
                ind = load_industry(iid)
                if ind:
                    t1 += ind.get("tier1_investors") or []
                    t2 += ind.get("tier2_investors") or []
        return _dedupe(t1), _dedupe(t2)


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for x in items:
        k = str(x)
        if k and k not in seen:
            seen.add(k)
            out.append(x)
    return out


def load_profile(path: Path | None = None) -> Profile:
    p = path or PROFILE_FILE
    if not p.exists():
        raise FileNotFoundError(
            f"找不到画像文件：{p}\n先运行 `radar init` 生成一份。"
        )
    return Profile(load_yaml(p))


# ---------------------------------------------------------------------------
#  公司池
# ---------------------------------------------------------------------------

@dataclass
class Watchlist:
    companies: list[dict] = field(default_factory=list)
    # 机构池（可选）：投投资岗时用来评估机构质量
    firms: list[dict] = field(default_factory=list)
    # 门户探测结果：id -> {type, arg, ...}
    portals: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for c in self.companies:
            entry = self.portals.get(c.get("id")) or {}
            if entry:
                c["portal_entry"] = entry
                if entry.get("type") in ("feishu", "moka", "beisen"):
                    c["portal"] = {
                        "type": entry["type"],
                        "arg": entry.get("arg", ""),
                        "note": f"发现于 {entry.get('verified_at', '')}（{entry.get('source', '')}）",
                    }

    def active(self) -> list[dict]:
        return [c for c in self.companies if c.get("status", "active") != "archived"]

    def active_firms(self) -> list[dict]:
        return [f for f in self.firms if f.get("status", "active") != "archived"]

    def with_portal(self) -> list[dict]:
        return [c for c in self.active() if c.get("portal")]

    def without_portal(self) -> list[dict]:
        return [c for c in self.active() if not c.get("portal")]

    def by_id(self, cid: str) -> dict | None:
        return next((c for c in self.companies if c.get("id") == cid), None)

    def stats(self) -> dict:
        act = self.active()
        return {
            "total": len(act),
            "with_portal": len(self.with_portal()),
            "without_portal": len(self.without_portal()),
            "firms": len(self.active_firms()),
            "by_city": _count_by(act, "city"),
            "by_confidence": _count_by(act, "confidence"),
        }


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        v = str(it.get(key) or "unknown")
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def load_watchlist() -> Watchlist:
    """公司池 + 机构池 + 门户探测结果。三个文件都是可选的。"""
    data = load_yaml(COMPANIES_FILE)
    companies = data.get("companies") or []
    firms = data.get("firms") or load_yaml(CONFIG_DIR / "firms.yaml").get("firms") or []
    portals = (load_yaml(PORTALS_FILE).get("portals")) or {}
    return Watchlist(companies=companies, firms=firms, portals=portals)
