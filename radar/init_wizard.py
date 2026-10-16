"""`radar init` —— 生成一份可用的求职画像。

支持交互式（有终端时）和非交互式（给自动化用）两种方式。
"""
from __future__ import annotations

import sys
from datetime import date

from .core import (
    CONFIG_DIR,
    COMPANIES_FILE,
    PORTALS_FILE,
    Profile,
    dump_yaml,
    ensure_dirs,
    list_industries,
    load_yaml,
    PROFILE_FILE,
)

CITY_PRESETS = {
    "1": ["北京"],
    "2": ["上海"],
    "3": ["深圳"],
    "4": ["杭州"],
    "5": ["北京", "上海"],
    "6": ["北京", "上海", "深圳", "杭州"],
}


def _default_profile(industry_ids: list[str], cities: list[str], candidate: dict | None = None) -> dict:
    return {
        "version": 1,
        "updated_at": date.today().isoformat(),
        "industries": industry_ids,
        "filters": {
            "cities": cities,
            "city_strict": False,
            "exclude_keywords": ["实习", "intern", "外包", "劳务派遣", "兼职", "校招", "校园", "应届", "优才", "届生"],
            "exclude_url_patterns": ["campusrecruitment", "/campus", "/school"],
            "min_salary_k": 0,
            "exclude_companies": [],
        },
        "tracks": {
            "industry": {
                "enabled": True,
                "label": "产业岗",
                "emoji": "🤖",
                "title_blocklist": ["投资顾问", "理财", "财富管理", "客户经理", "保险", "房产", "地摊", "电话销售", "客服"],
                "weights": {"title_match": 15, "quality": 22, "body_fit": 20,
                            "experience": 28, "freshness": 5, "city": 10},
            },
            "investment": {
                "enabled": False,
                "label": "投资岗",
                "emoji": "💼",
                "title_blocklist": ["投资顾问", "理财", "财富管理", "客户经理", "投资拓展"],
                "weights": {"title_match": 15, "quality": 22, "body_fit": 20,
                            "experience": 28, "freshness": 5, "city": 10},
            },
        },
        "quality": {"funding_recency": [
            {"max_days": 90, "score": 100},
            {"max_days": 180, "score": 80},
            {"max_days": 365, "score": 55},
            {"max_days": 730, "score": 30},
            {"max_days": 99999, "score": 10},
        ]},
        "experience_gate": {
            "unspecified": 72,
            "bands": [
                {"max_years": 1, "score": 100},
                {"max_years": 2, "score": 95},
                {"max_years": 3, "score": 88},
                {"max_years": 5, "score": 68},
                {"max_years": 8, "score": 38},
                {"max_years": 99, "score": 15},
            ],
            "hard_gate_keywords": ["博士", "10年以上", "十年以上"],
            "hard_gate_score": 20,
        },
        "candidate": candidate or {
            "strength_keywords": ["沟通", "协作", "项目管理", "数据分析", "英语", "落地", "跨部门"],
            "gap_keywords": [],
        },
        "output": {
            "top_n_per_track": 25,
            "min_score": 0,
            "alert_score": 80,
            "keep_reports_days": 90,
        },
    }


def _ask(prompt: str, default: str = "") -> str:
    try:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return v or default


def run_init(industry: str | None = None, cities: list[str] | None = None,
             non_interactive: bool = False, force: bool = False) -> Profile:
    ensure_dirs()

    if PROFILE_FILE.exists() and not force:
        print(f"画像已存在：{PROFILE_FILE}")
        print("想重新生成请加 --force。")
        return Profile(load_yaml(PROFILE_FILE))

    all_inds = list_industries()
    ids = [i["id"] for i in all_inds]

    # ---- 选行业 ----
    if industry:
        chosen = [x.strip() for x in industry.split(",") if x.strip() in ids]
        if not chosen:
            print(f"未知的行业 id：{industry}\n可选项：{', '.join(ids)}")
            sys.exit(1)
    elif non_interactive or not sys.stdin.isatty():
        chosen = ["general"]
    else:
        print("\n可选的行业包：\n")
        for n, i in enumerate(all_inds, 1):
            print(f"  {n:2d}. {i.get('name')}  ({i.get('name_en')})")
            if i.get("description"):
                print(f"      {i['description']}")
        raw = _ask("\n选行业（可多选，用逗号分隔序号）", "1")
        chosen = []
        for tok in raw.replace("，", ",").split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(all_inds):
                chosen.append(all_inds[int(tok) - 1]["id"])
        chosen = chosen or ["general"]

    # ---- 选城市 ----
    if cities:
        city_list = cities
    elif non_interactive or not sys.stdin.isatty():
        city_list = ["北京"]
    else:
        print("\n目标城市：")
        print("  1. 北京    2. 上海    3. 深圳    4. 杭州")
        print("  5. 北京+上海      6. 北上深杭")
        raw = _ask("选择", "1")
        city_list = CITY_PRESETS.get(raw.strip())
        if not city_list:
            city_list = [c.strip() for c in raw.replace("，", ",").split(",") if c.strip()] or ["北京"]

    # ---- 选岗位轨道 ----
    want_investment = False
    if not (non_interactive or not sys.stdin.isatty()):
        ans = _ask("\n是否同时关注「投资岗」（VC/PE/CVC 的投资经理、投研等）？(y/N)", "n")
        want_investment = ans.lower().startswith("y")

    profile = _default_profile(chosen, city_list)
    profile["tracks"]["investment"]["enabled"] = want_investment

    dump_yaml(profile, PROFILE_FILE)
    if not COMPANIES_FILE.exists():
        dump_yaml({"meta": {"note": "在这里添加你想盯的公司。加一条 = 多盯一家。",
                            "fields": "id/name/city/tracks/funding/tier1_backers/portal/priority"},
                   "companies": []}, COMPANIES_FILE)
    if not PORTALS_FILE.exists():
        dump_yaml({"meta": {"note": "招聘门户探测结果，由 `radar discover` 或 AI 写入"},
                   "portals": {}}, PORTALS_FILE)

    print(f"\n✓ 画像已生成：{PROFILE_FILE}")
    print(f"  行业：{', '.join(chosen)}")
    print(f"  城市：{', '.join(city_list)}")
    print(f"  岗位轨道：产业岗{'、投资岗' if want_investment else ''}")
    print(f"\n下一步：")
    print(f"  1. 编辑 {COMPANIES_FILE} 加入你想盯的公司")
    print(f"  2. 运行 `radar setup` 安装抓取引擎")
    print(f"  3. 运行 `radar run`")
    return Profile(profile)
