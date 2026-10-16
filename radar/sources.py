"""企业官方招聘门户抓取。

中国主流招聘系统（飞书招聘 / Moka / 北森）都有公开的岗位接口，
本模块封装 Hiring-Radar（MIT）作为解析引擎，把企业自己挂的岗位抓下来 ——
比抓招聘平台更稳、更全、也更合规（只读公开信息，不碰简历）。

Hiring-Radar 不随仓库分发，首次使用时由 `radar setup` 或本模块自动克隆到
~/.cn-job-radar/vendor/Hiring-Radar。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .core import CACHE_DIR, DATA_DIR, HOME, Watchlist
from .scorer import Job

VENDOR_DIR = HOME / "vendor"
RADAR_DIR = VENDOR_DIR / "Hiring-Radar"
RADAR_CLI = RADAR_DIR / "hiring_radar.py"
SEED_FILE = RADAR_DIR / "parsers" / "companies.seed"
RADAR_HOME = HOME / "vendor" / ".radar-home"
REPO = "https://github.com/simonlin1212/Hiring-Radar.git"

PYTHON = os.environ.get("JOB_RADAR_PYTHON") or sys.executable


def _env() -> dict:
    e = dict(os.environ)
    e["HIRING_RADAR_HOME"] = str(RADAR_HOME)
    e.setdefault("HIRING_RADAR_DELAY", "0.3")
    # 本地与国内站点不要走代理，否则请求可能被改写
    e["no_proxy"] = "127.0.0.1,localhost," + e.get("no_proxy", "")
    e["NO_PROXY"] = e["no_proxy"]
    return e


def is_installed() -> bool:
    return RADAR_CLI.exists()


def install(quiet: bool = False) -> bool:
    """克隆抓取引擎。返回是否可用。"""
    if is_installed():
        return True
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone", "--depth", "1", REPO, str(RADAR_DIR)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        if not quiet:
            print(f"  [!] 无法克隆抓取引擎：{exc}")
        return False
    if p.returncode != 0:
        if not quiet:
            print(f"  [!] git clone 失败：{p.stderr.strip()[:200]}")
        return False
    if not quiet:
        print(f"  ✓ 抓取引擎已安装到 {RADAR_DIR}")
    return True


def sync_seed(watchlist: Watchlist) -> int:
    """把公司池里配了门户的公司写进引擎的种子表。返回新增条数。"""
    if not SEED_FILE.exists():
        return 0

    lines = SEED_FILE.read_text(encoding="utf-8").splitlines()
    existing = {ln.split("|")[0].strip() for ln in lines if "|" in ln and not ln.strip().startswith("#")}

    added: list[str] = []
    for c in watchlist.with_portal():
        key = str(c.get("id") or "").strip()
        portal = c.get("portal") or {}
        ptype = str(portal.get("type") or "").strip()
        arg = str(portal.get("arg") or "").strip()
        if not key or key in existing:
            continue
        seg = (c.get("tracks") or ["其他"])[0] if c.get("tracks") else "其他"
        if ptype == "feishu":
            added.append(f"{key} | feishu | {c.get('name')} | {arg} |  | {seg}")
        elif ptype == "moka":
            parts = (arg.split(",") + [""])[:2]
            added.append(f"{key} | moka | {c.get('name')} | {parts[0].strip()} | {parts[1].strip()} | {seg}")
        elif ptype == "beisen":
            added.append(f"{key} | beisen | {c.get('name')} | {arg} |  | {seg}")

    if added:
        with SEED_FILE.open("a", encoding="utf-8") as f:
            f.write("\n# --- 由 cn-job-radar 自动同步，请勿手工编辑以下内容 ---\n")
            f.write("\n".join(added) + "\n")
    return len(added)


def _run(args: list[str], timeout: int = 180) -> dict | None:
    try:
        p = subprocess.run(
            [PYTHON, str(RADAR_CLI)] + args,
            cwd=str(RADAR_DIR), env=_env(),
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    out = p.stdout.strip()
    i = out.find("{")
    if i < 0:
        return None
    try:
        return json.loads(out[i:])
    except json.JSONDecodeError:
        return None


def _to_date(value) -> str:
    """引擎的 date 字段有时是毫秒时间戳（int）。"""
    if isinstance(value, (int, float)):
        if value <= 0:
            return ""
        ts = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return ""
    return str(value or "").strip()


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def fetch_company(company: dict, recent_days: int = 365) -> list[Job]:
    key = company.get("id")
    if not key:
        return []
    payload = _run(["--local", str(key), "--json", "--recent-days", str(recent_days)])
    if not payload:
        return []
    jobs = []
    for r in payload.get("jobs") or []:
        jobs.append(Job(
            title=_s(r.get("title")),
            company=company.get("name") or _s(r.get("company")),
            company_id=str(key),
            location=_s(r.get("location")),
            salary=_s(r.get("comp")),
            posted=_to_date(r.get("date")) or _to_date(r.get("date_updated")),
            url=_s(r.get("url")) or _s(r.get("apply_url")),
            jd=_s(r.get("jd")),
            source="企业官方门户",
            dept=_s(r.get("dept")) or _s(r.get("team")),
        ))
    return jobs


def fetch_all(watchlist: Watchlist, workers: int = 4, recent_days: int = 365,
              on_progress=None) -> tuple[list[Job], dict[str, dict]]:
    """并发抓取所有配了门户的公司。返回 (岗位, 每个门户的健康度)。"""
    targets = watchlist.with_portal()
    jobs: list[Job] = []
    health: dict[str, dict] = {}
    if not targets:
        return jobs, health

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_company, c, recent_days): c for c in targets}
        for f in as_completed(futs):
            c = futs[f]
            name = c.get("name") or c.get("id")
            try:
                got = f.result()
            except Exception as exc:  # noqa: BLE001
                health[str(c.get("id"))] = {"name": name, "count": -1, "status": "error", "note": str(exc)[:120]}
                if on_progress:
                    on_progress(name, -1)
                continue
            status = "ok" if got else "empty"
            health[str(c.get("id"))] = {"name": name, "count": len(got), "status": status, "note": ""}
            if on_progress:
                on_progress(name, len(got))
            jobs.extend(got)

    _save_health(health)
    return jobs, health


def _save_health(health: dict[str, dict]) -> None:
    """持久化门户健康度，并标出与上次相比的状态变化。

    门户突然清空往往是重要信号（招聘冻结，或岗位迁到别处），值得单独告警。
    """
    path = DATA_DIR / "portal_health.json"
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prev = {}

    for cid, rec in health.items():
        old = (prev.get("current") or {}).get(cid) or {}
        rec["prev_status"] = old.get("status", "")
        rec["changed"] = bool(old) and old.get("status") != rec["status"]
        rec["checked_at"] = datetime.now().isoformat(timespec="seconds")

    history = prev.get("history") or []
    history.append({"at": datetime.now().isoformat(timespec="seconds"),
                    "summary": {k: v["status"] for k, v in health.items()}})
    path.write_text(
        json.dumps({"current": health, "history": history[-180:]}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def health_alerts() -> list[str]:
    path = DATA_DIR / "portal_health.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    alerts = []
    for rec in (data.get("current") or {}).values():
        if not rec.get("changed"):
            continue
        if rec["status"] == "empty":
            alerts.append(f"⚠️ {rec['name']}：官方门户变为「无在招岗位」")
        elif rec["status"] == "ok":
            alerts.append(f"✅ {rec['name']}：门户已恢复，现有 {rec['count']} 个岗位")
        else:
            alerts.append(f"❌ {rec['name']}：门户抓取异常")
    return alerts


# ---------------------------------------------------------------------------
#  门户探测：给还没有门户的公司找入口
# ---------------------------------------------------------------------------

PORTAL_CANDIDATES = {
    "feishu": [
        "https://{slug}.jobs.feishu.cn",
        "https://{slug}.jobs.feishu.cn/index",
    ],
    "beisen": ["https://{slug}.zhiye.com"],
    "moka": ["https://{slug}.mokahr.com"],
}


def probe_candidates(slugs: list[str], timeout: int = 12) -> dict[str, list[dict]]:
    """用常见 slug 猜测门户并验证可达性。

    注意：飞书门户的 slug 多数是随机串，猜不出来 —— 这一招主要对北森
    （<英文名>.zhiye.com）和少数用英文名的飞书门户有效。
    真正的覆盖率还得靠检索，或让 AI 去公司官网找跳转链接。
    """
    import urllib.error
    import urllib.request

    found: dict[str, list[dict]] = {}
    for slug in slugs:
        slug = slug.strip().lower()
        if not slug:
            continue
        for ptype, tmpls in PORTAL_CANDIDATES.items():
            for tmpl in tmpls:
                url = tmpl.format(slug=slug)
                code = _http_status(url, timeout)
                if code == 200:
                    found.setdefault(slug, []).append({"type": ptype, "url": url, "status": code})
                    break
    return found


def _http_status(url: str, timeout: int = 12) -> int:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; cn-job-radar)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0
