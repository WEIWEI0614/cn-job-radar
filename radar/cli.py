"""命令行入口。

    radar init        生成求职画像（交互式）
    radar setup       安装抓取引擎
    radar run         抓取 → 打分 → 出日报
    radar stats       查看公司池状况
    radar discover    探测招聘门户
    radar ui          启动本地 Web 界面
    radar industries  列出内置行业包
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report as report_mod
from . import sources
from .core import (
    DATA_DIR,
    REPORT_DIR,
    ensure_dirs,
    list_industries,
    load_profile,
    load_watchlist,
)
from .init_wizard import run_init
from .scorer import Job, score_all

BANNER = "cn-job-radar · 求职雷达"


def _load_external_jobs() -> list[Job]:
    """读取 data/raw/*.json —— 由你自己或 AI 抓来后写进去的岗位。"""
    jobs: list[Job] = []
    for p in sorted((DATA_DIR / "raw").glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"  [!] 跳过 {p.name}：{exc}")
            continue
        records = data if isinstance(data, list) else data.get("jobs", [])
        for r in records:
            jobs.append(Job.from_dict(r, source=p.stem))
    return jobs


def cmd_init(args) -> int:
    run_init(industry=args.industry, cities=args.cities, non_interactive=args.yes, force=args.force)
    return 0


def cmd_setup(args) -> int:
    print(f"{BANNER}\n")
    ok = sources.install(quiet=False)
    return 0 if ok else 1


def cmd_industries(args) -> int:
    inds = list_industries()
    print(f"\n内置行业包（{len(inds)} 个）：\n")
    for i in inds:
        print(f"  {i['id']:<16} {i.get('name', '')}  ·  {i.get('name_en', '')}")
        if i.get("description"):
            print(f"  {'':<16} {i['description']}")
    print(f"\n用法：radar init --industry {inds[0]['id'] if inds else 'ai'}")
    print("自定义：把同名 yaml 放到 ~/.cn-job-radar/config/industries/ 即可覆盖内置版本。\n")
    return 0


def cmd_stats(args) -> int:
    profile = load_profile()
    wl = load_watchlist()
    st = wl.stats()

    print(f"\n📊 {BANNER}\n")
    print(f"  行业：{'、'.join(profile.industries) or '未指定'}")
    print(f"  城市：{'、'.join(profile.hard_filters.get('cities') or ['不限'])}")
    print(f"  轨道：{'、'.join(profile.enabled_tracks())}")
    print(f"\n  公司池：{st['total']} 家（可自动抓取 {st['with_portal']} 家）")
    if st["firms"]:
        print(f"  机构池：{st['firms']} 家")
    if st["by_city"]:
        print("\n  按城市：")
        for k, v in list(st["by_city"].items())[:10]:
            print(f"    {k}: {v}")

    missing = wl.without_portal()
    if missing:
        print(f"\n  ⚠️ 以下 {len(missing)} 家公司还没有招聘门户，只能用检索覆盖：")
        for c in missing[:30]:
            print(f"    · {c.get('name')}（{c.get('city', '')}）")
        if len(missing) > 30:
            print(f"    … 还有 {len(missing) - 30} 家")
        print("\n  提示：用 `radar discover` 或让 AI 去官网找招聘入口，写进 portals.yaml 即可。")

    reports = sorted(REPORT_DIR.glob("*.html"))
    print(f"\n  最近一次日报：{reports[-1].name if reports else '还没有'}\n")
    return 0


def cmd_discover(args) -> int:
    wl = load_watchlist()
    if args.slugs:
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        # 用还没有门户的公司名/拼音当候选 slug
        slugs = []
        for c in wl.without_portal()[:20]:
            for key in ("slug", "id"):
                if c.get(key):
                    slugs.append(str(c[key]))

    if not slugs:
        print("没有可探测的候选。用 --slugs a,b,c 指定要试的域名前缀。")
        return 0

    print(f"\n🔍 探测 {len(slugs)} 个候选 slug …\n")
    found = sources.probe_candidates(slugs)
    if not found:
        print("  没有命中。")
        print("  说明：飞书门户的域名多是随机串（如 k0fqxcszc9.jobs.feishu.cn），猜不出来；")
        print("        北森是 <英文名>.zhiye.com，成功率高；Moka 的 slug 也不规则。")
        print("        大规模补齐还得靠检索 —— 见 README 的「补齐招聘入口」一节。\n")
        return 0

    for slug, hits in found.items():
        for h in hits:
            print(f"  ✓ {slug:<20} {h['type']:<8} {h['url']}")
    print(f"\n  把结果写进 {DATA_DIR.parent / 'config' / 'portals.yaml'} 的 portals 段即可生效。\n")
    return 0


def cmd_run(args) -> int:
    ensure_dirs()
    profile = load_profile()
    wl = load_watchlist()

    print(f"{BANNER}\n   行业：{'、'.join(profile.industries) or '未指定'} · 轨道：{'、'.join(profile.enabled_tracks())}")

    source_status: dict[str, str] = {}
    jobs: list[Job] = []

    # 1) 抓企业官方门户
    if not args.no_fetch:
        targets = wl.with_portal()
        if not targets:
            source_status["企业官方门户"] = "⚠️ 公司池里还没有配置门户的公司"
        elif not sources.is_installed():
            source_status["企业官方门户"] = "⚠️ 抓取引擎未安装，先运行 `radar setup`"
        else:
            added = sources.sync_seed(wl)
            print(f"\n📡 抓取官方门户（{len(targets)} 家）…" + (f" 同步 {added} 条新配置" if added else ""))
            got, health = sources.fetch_all(
                wl, on_progress=lambda n, c: print(f"  [{n}] {'抓取失败' if c < 0 else f'{c} 个岗位'}")
            )
            jobs.extend(got)
            empty = [h["name"] for h in health.values() if h["status"] == "empty"]
            err = [h["name"] for h in health.values() if h["status"] == "error"]
            msg = f"✅ {len(targets)} 家尝试，取回 {len(got)} 个岗位"
            if empty:
                msg += f"；⚠️ {len(empty)} 家门户无在招岗位（{'、'.join(empty[:4])}{'…' if len(empty) > 4 else ''}）"
            if err:
                msg += f"；❌ {len(err)} 家抓取异常"
            source_status["企业官方门户"] = msg
    else:
        source_status["企业官方门户"] = "⏸ 已跳过（--no-fetch）"

    # 2) 并入外部岗位
    if args.ingest:
        dst = DATA_DIR / "raw" / Path(args.ingest).name
        dst.write_text(Path(args.ingest).read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  已并入 {dst.name}")

    ext = _load_external_jobs()
    if ext:
        jobs.extend(ext)
        by_src: dict[str, int] = {}
        for j in ext:
            by_src[j.source] = by_src.get(j.source, 0) + 1
        for k, v in sorted(by_src.items(), key=lambda kv: -kv[1])[:6]:
            source_status[f"外部源 · {k}"] = f"✅ {v} 个岗位"
    else:
        source_status["外部岗位文件"] = "⏸ data/raw/ 为空 —— 把检索到的岗位写成 JSON 放进去就会自动并入"

    print(f"\n🧮 共 {len(jobs)} 个原始岗位，开始打分…")
    scored, rejected = score_all(jobs, profile, wl)
    if args.track:
        scored = [s for s in scored if s.track == args.track]
    min_score = float(profile.output.get("min_score", 0))
    scored = [s for s in scored if s.score >= min_score]
    print(f"   通过筛选：{len(scored)} 条（淘汰 {rejected} 条）")

    notes = sources.health_alerts() if not args.no_fetch else []
    paths = report_mod.write_report(scored, profile, wl, source_status, notes)

    print("\n📄 报告已生成：")
    for k, v in paths.items():
        print(f"   {k:<5} → {v}")

    alert = float(profile.output.get("alert_score", 80))
    hot = [s for s in scored if s.score >= alert]
    if hot:
        print(f"\n🔥 高优先级 {len(hot)} 条：")
        for s in hot[:10]:
            print(f"   {s.score:5.1f}  {s.job.title} @ {s.job.company}")
    print()
    return 0


def cmd_ui(args) -> int:
    from .ui.server import serve
    ensure_dirs()
    serve(port=args.port, open_browser=not args.no_open)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="radar", description=f"{BANNER} —— 盯公司官方招聘门户，而不是招聘平台")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="生成求职画像")
    a.add_argument("--industry", help=f"行业包 id，多个用逗号分隔（可选：{'、'.join(i['id'] for i in list_industries()[:8])}…）")
    a.add_argument("--cities", nargs="*", help="目标城市，如 --cities 北京 上海")
    a.add_argument("--yes", action="store_true", help="非交互模式，全部用默认值")
    a.add_argument("--force", action="store_true", help="覆盖已存在的画像")
    a.set_defaults(func=cmd_init)

    a = sub.add_parser("setup", help="安装抓取引擎（首次使用）")
    a.set_defaults(func=cmd_setup)

    a = sub.add_parser("run", help="抓取 → 打分 → 出日报")
    a.add_argument("--no-fetch", action="store_true", help="不联网抓取，只用已有数据重新打分")
    a.add_argument("--track", help="只输出指定轨道")
    a.add_argument("--ingest", help="并入一个外部岗位 JSON 文件")
    a.set_defaults(func=cmd_run)

    a = sub.add_parser("stats", help="查看公司池状况")
    a.set_defaults(func=cmd_stats)

    a = sub.add_parser("discover", help="探测招聘门户")
    a.add_argument("--slugs", help="要试的域名前缀，逗号分隔")
    a.set_defaults(func=cmd_discover)

    a = sub.add_parser("ui", help="启动本地 Web 界面")
    a.add_argument("--port", type=int, default=8787)
    a.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    a.set_defaults(func=cmd_ui)

    a = sub.add_parser("industries", help="列出内置行业包")
    a.set_defaults(func=cmd_industries)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"\n{exc}\n")
        return 2
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
