"""冒烟测试。全部离线，不联网。

    pytest -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把数据目录切到临时目录，避免污染真实配置。"""
    monkeypatch.setenv("JOB_RADAR_HOME", str(tmp_path))
    for mod in [m for m in list(sys.modules) if m.startswith("radar")]:
        del sys.modules[mod]
    import radar.core as core
    core.HOME = tmp_path
    core.CONFIG_DIR = tmp_path / "config"
    core.DATA_DIR = tmp_path / "data"
    core.REPORT_DIR = tmp_path / "reports"
    core.CACHE_DIR = tmp_path / "data" / "cache"
    core.PROFILE_FILE = core.CONFIG_DIR / "profile.yaml"
    core.COMPANIES_FILE = core.CONFIG_DIR / "companies.yaml"
    core.PORTALS_FILE = core.CONFIG_DIR / "portals.yaml"
    core.ensure_dirs()
    return core


# ---------------------------------------------------------------------------
#  行业包
# ---------------------------------------------------------------------------

def test_industries_load():
    from radar.core import list_industries
    inds = list_industries()
    ids = {i["id"] for i in inds}
    assert {"ai", "robotics", "general"} <= ids, "内置行业包缺失"
    for i in inds:
        assert i.get("job_keywords", {}).get("strong"), f"{i['id']} 缺少 strong 关键词"


def test_industry_packs_have_required_fields():
    from radar.core import list_industries
    for i in list_industries():
        assert i.get("name"), f"{i['id']} 缺 name"
        assert isinstance(i.get("job_keywords", {}).get("normal"), list), f"{i['id']} normal 不是列表"
        assert isinstance(i.get("tier1_investors"), list), f"{i['id']} 缺 tier1_investors"


# ---------------------------------------------------------------------------
#  画像
# ---------------------------------------------------------------------------

def test_init_and_profile_merge(sandbox):
    from radar.init_wizard import run_init
    from radar.core import load_profile

    run_init(industry="robotics", cities=["北京"], non_interactive=True)
    p = load_profile()

    assert p.industries == ["robotics"]
    assert p.hard_filters["cities"] == ["北京"]

    merged = p.merged_track("industry")
    assert merged["title_keywords"]["strong"], "行业包关键词没有合并进来"
    assert "产品经理" in merged["title_keywords"]["strong"]
    # 行业词应该落在 normal 档，否则硬核技术岗会被误判成高优先级
    assert "机器人" in merged["title_keywords"]["normal"]


def test_tier_investors_merged_from_industry(sandbox):
    from radar.init_wizard import run_init
    from radar.core import load_profile

    run_init(industry="robotics", cities=["北京"], non_interactive=True)
    t1, _t2 = load_profile().merged_tier_investors()
    assert "红杉中国" in t1


# ---------------------------------------------------------------------------
#  打分
# ---------------------------------------------------------------------------

def _scorer(sandbox):
    from radar.core import Profile, Watchlist
    from radar.init_wizard import run_init
    run_init(industry="robotics", cities=["北京"], non_interactive=True)
    from radar.core import load_profile
    return load_profile(), Watchlist()


def test_experience_gate_prefers_low_bar():
    """经验门槛：低要求岗位必须得分更高 —— 这是本项目的核心设计。"""
    from radar.scorer import _experience_score
    from radar.core import Profile

    prof = Profile({"experience_gate": {
        "unspecified": 72,
        "bands": [{"max_years": 1, "score": 100}, {"max_years": 3, "score": 88},
                  {"max_years": 5, "score": 68}, {"max_years": 99, "score": 15}],
        "hard_gate_keywords": ["博士"], "hard_gate_score": 20,
    }})

    low, _ = _experience_score("要求：1年以上经验", "交付工程师", prof)
    mid, _ = _experience_score("要求：3年以上经验", "产品经理", prof)
    high, _ = _experience_score("要求：8年以上经验", "架构师", prof)
    phd, note = _experience_score("要求：博士学历，5年以上经验", "算法研究员", prof)
    none, _ = _experience_score("负责产品规划与需求分析", "产品经理", prof)

    assert low == 100 and mid == 88 and high == 15
    assert phd == 20 and "博士" in note, "博士应触发硬门槛"
    assert none == 72, "没写年限时应给中性分"
    assert low > mid > high > 0


def test_min_required_years():
    from radar.scorer import _min_required_years
    assert _min_required_years("3年以上工作经验") == 3
    assert _min_required_years("要求 3-5 年经验") == 3
    assert _min_required_years("1年以上，5年经验优先") == 1, "应取最小门槛"
    assert _min_required_years("无年限要求") is None
    assert _min_required_years("公司成立于2015年") is None, "年份不应被误判"


def test_city_score_multilocation():
    """北京+上海的双地点岗位不该拿满分，否则外地岗位会挤满榜单。"""
    from radar.scorer import _city_score
    from radar.core import Profile

    prof = Profile({"filters": {"cities": ["北京"]}})
    assert _city_score("北京", prof) == 100
    assert _city_score("北京-海淀", prof) == 100
    assert _city_score("上海, 北京", prof) == 78
    assert _city_score("上海", prof) == 20


def test_hard_filter_excludes_campus():
    from radar.scorer import hard_filter, Job
    from radar.core import Profile

    prof = Profile({"filters": {
        "exclude_keywords": ["校招", "应届"],
        "exclude_url_patterns": ["campusrecruitment"],
    }})
    assert not hard_filter(Job(title="视觉算法工程师（校招）"), prof)[0]
    assert not hard_filter(Job(title="产品经理", url="https://x.com/campusrecruitment/a"), prof)[0]
    assert hard_filter(Job(title="产品经理"), prof)[0]


def test_hard_filter_exclude_matches_title_only():
    """排除词只匹配岗位名 —— 否则 JD 里一句「欢迎应届生」会误杀整个岗位。"""
    from radar.scorer import hard_filter, Job
    from radar.core import Profile

    prof = Profile({"filters": {"exclude_keywords": ["应届"]}})
    ok, _ = hard_filter(Job(title="产品经理", jd="欢迎应届生投递，也接受有经验者"), prof)
    assert ok, "JD 正文里的排除词不应误杀岗位"


def test_salary_parsing():
    from radar.scorer import _salary_lower_bound_k
    assert _salary_lower_bound_k("30-60k·18薪") == 30
    assert _salary_lower_bound_k("25-50K") == 25
    assert _salary_lower_bound_k("1.5-3.5万") == 15
    assert _salary_lower_bound_k("") is None


# ---------------------------------------------------------------------------
#  端到端
# ---------------------------------------------------------------------------

def test_end_to_end_scoring_and_report(sandbox):
    import json
    from radar.core import dump_yaml, load_profile, load_watchlist
    from radar.init_wizard import run_init
    from radar.scorer import Job, score_all
    from radar import report as report_mod

    run_init(industry="robotics", cities=["北京"], non_interactive=True)
    dump_yaml({"companies": [{
        "id": "acme", "name": "示例机器人", "city": "北京", "priority": 1,
        "confidence": "verified", "status": "active",
        "funding": {"latest_round": "B轮", "amount": "10亿元", "date": "2026-05-01"},
        "tier1_backers": ["红杉中国", "经纬创投", "蓝驰创投"],
    }]}, sandbox.COMPANIES_FILE)

    profile, wl = load_profile(), load_watchlist()
    jobs = [
        Job(title="具身智能产品经理", company="示例机器人", company_id="acme",
            location="北京", posted="2026-10-15", url="https://e.com/1",
            jd="负责机器人产品定义。要求：2年以上经验，硕士学历，数学或计算机背景优先。"),
        Job(title="大模型算法研究员", company="示例机器人", company_id="acme",
            location="北京", posted="2026-10-15", url="https://e.com/2",
            jd="要求：博士学历，8年以上经验。"),
        Job(title="数据分析实习生", company="示例机器人", company_id="acme",
            location="北京", url="https://e.com/3", jd="实习岗"),
    ]
    scored, rejected = score_all(jobs, profile, wl)

    assert rejected >= 1, "实习生应被过滤掉"
    assert len(scored) >= 2
    by_title = {s.job.title: s for s in scored}
    assert by_title["具身智能产品经理"].score > by_title["大模型算法研究员"].score, \
        "低门槛商业岗应高于高门槛硬核技术岗"

    paths = report_mod.write_report(scored, profile, wl, {"测试源": "ok"}, [])
    doc = paths["html"].read_text(encoding="utf-8")
    assert doc.startswith("<!DOCTYPE html>") and doc.rstrip().endswith("</html>")
    assert "{" not in doc.split("<style>")[0].replace("<!DOCTYPE html>", ""), "HTML 里有未渲染的占位符"

    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["jobs"] and all("score" in j for j in data["jobs"])
