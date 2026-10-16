#!/usr/bin/env python3
"""从一份私人工作区导出「脱敏种子池」，用于开源分享或团队共用。

用法：
    python tools/export_seed.py --src ~/Desktop/孵化器/求职雷达/config \
                                --out examples/seed

脱敏规则（重点）：
  1. 删掉 notes / source 里含「你 / 您」的**句子片段**（那些句子往往是写给
     某个具体使用者的建议，会暴露他的背景、学历、职业规划）。
     实现方式是两级切分：先按句号/分号切，再按逗号切，只丢弃含人称的片段，
     尽量保住其余事实性内容。
  2. 丢掉 meta 里的个人化字段。
  3. 公司名、融资数据、投资方、招聘门户这些**全部是公开信息**，原样保留。

导出前会打印所有被改动的字段，方便人工复核一遍再提交。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# 第一人称 / 第二人称。注意「加入我们」是页面名，不能当成第一人称误杀，
# 所以用 lookbehind 把紧跟「加入」的「我」排除掉。
SELF_RE = re.compile(r"你|您|(?<!加入)我")
# 额外的「个人痕迹」词。
# ⚠️ 刻意**不**列举任何具体学校 / 公司 / 城市名 —— 把某个使用者的履历写进
#    开源代码，本身就是一种泄露。这里只放与具体身份无关的通用表述。
IDENTITY_RE = re.compile(
    r"本人|个人简历|求职信|面试复盘|职业规划|跳槽|转行打算|家庭因素|"
    r"期望薪资|当前学历|个人情况|简历上"
)

SEED_HEADER = """# {title}
# ---------------------------------------------------------------------------
# 这是「{name}」赛道的社区种子公司池 —— 直接复制到
#   ~/.cn-job-radar/config/companies.yaml
# 就能用。
#
# 公司名、融资轮次、投资方、招聘门户都是**公开信息**，来自企业公告、
# 权威媒体与门户实测。
#
# 想加公司：在 companies 列表下追加一条即可，字段说明见 examples/companies.example.yaml。
# 想改门户：改 portal 段；或写进 portals.yaml 让 `radar discover` 维护。
#
# ⚠️ 免责：融资数据随融资进展变化，投递前请以公司官方披露为准。
# ---------------------------------------------------------------------------
"""


def _scrub(text: str | None) -> tuple[str | None, bool]:
    """删掉含人称代词的句子片段。返回 (清洗后文本, 是否改动过)。"""
    if not text:
        return text, False
    original = str(text)
    if not SELF_RE.search(original) and not IDENTITY_RE.search(original):
        return original, False

    kept_sentences: list[str] = []
    for sent in re.split(r"(?<=[。；;])", original):
        if not sent.strip():
            continue
        if SELF_RE.search(sent) or IDENTITY_RE.search(sent):
            # 二级切分：按逗号与破折号拆开，只丢弃含人称的片段，尽量保住事实性内容
            # （破折号后面往往才是那句「给你的建议」，前面的事实部分应当留下）
            pieces = [p for p in re.split(r"(?<=[，,])|——", sent) if p.strip()]
            survivors = [p.strip() for p in pieces if not SELF_RE.search(p) and not IDENTITY_RE.search(p)]
            merged = ""
            for p in survivors:
                if merged and not merged.endswith(("，", "、", "。", "；", ";", "：")):
                    merged += "，"
                merged += p
            merged = merged.strip()
            if merged:
                kept_sentences.append(merged if merged.endswith(("。", "；", ";")) else merged + "。")
        else:
            kept_sentences.append(sent)

    out = "".join(kept_sentences)
    out = re.sub(r"^[，,。；;、\s—\-·]+", "", out)
    out = re.sub(r"[，,。；;、\s—\-·]+$", "", out)
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"\s{2,}", " ", out)
    return (out or None), True


def scrub_obj(obj, path: str = "", log: list | None = None):
    """递归清洗 dict / list / str。"""
    log = log if log is not None else []
    if isinstance(obj, dict):
        return {k: scrub_obj(v, f"{path}.{k}" if path else k, log) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v, f"{path}[{i}]", log) for i, v in enumerate(obj)]
    if isinstance(obj, str):
        cleaned, changed = _scrub(obj)
        if changed:
            log.append((path, obj, cleaned))
        return cleaned
    return obj


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump(data: dict, path: Path, header: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        if header:
            f.write(header)
            f.write("\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False, width=110)


def main() -> int:
    ap = argparse.ArgumentParser(description="导出去敏的种子公司池")
    ap.add_argument("--src", required=True, type=Path, help="源 config 目录")
    ap.add_argument("--out", required=True, type=Path, help="输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要改动的内容，不写文件")
    args = ap.parse_args()

    src, out = args.src.expanduser(), args.out
    all_log: list[tuple] = []

    jobs = [
        ("companies.yaml", "robotics", "机器人 · 具身智能", "Companies · Robotics & Embodied AI"),
        ("ai_companies.yaml", "ai", "人工智能 · 大模型", "Companies · AI & LLM"),
        ("institutions.yaml", "institutions", "创投机构", "Institutions · VC / CVC / State Funds"),
    ]

    for fname, out_name, cn, en in jobs:
        raw = load(src / fname)
        if not raw:
            print(f"  · 跳过 {fname}（不存在）")
            continue

        # meta 里的个人化字段一律不导出
        raw.pop("meta", None)
        log: list[tuple] = []
        cleaned = scrub_obj(raw, fname, log)
        all_log.extend(log)

        n = len(cleaned.get("companies") or cleaned.get("institutions") or [])
        print(f"  ✓ {fname:<22} → {out_name}.yaml  （{n} 条，脱敏 {len(log)} 处）")

        if not args.dry_run:
            dump(cleaned, out / f"{out_name}.yaml",
                 SEED_HEADER.format(title=f"{cn} / {en}", name=cn))

    # 门户配置：只保留可自动抓取的 ATS 类型，web/email/blocked 对别人没用
    portals_raw = load(src / "portals.yaml")
    portals = portals_raw.get("portals") or {}
    keep = {}
    for cid, entry in portals.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in ("feishu", "moka", "beisen"):
            e = {k: v for k, v in entry.items() if k in ("type", "arg", "http_status", "verified_at", "source")}
            e["source"], _ = _scrub(e.get("source", ""))
            keep[cid] = e
    if not args.dry_run and keep:
        dump({"portals": keep}, out / "portals.yaml",
             SEED_HEADER.format(title="招聘门户 / Discovered ATS portals", name="已验证门户"))
    print(f"  ✓ portals.yaml           → portals.yaml  （{len(keep)} 个可自动抓取的门户）")

    if all_log:
        print(f"\n{'=' * 70}\n脱敏明细（共 {len(all_log)} 处，请人工复核）\n{'=' * 70}")
        for path, before, after in all_log:
            print(f"\n[{path}]")
            print(f"  原：{before}")
            print(f"  改：{after if after else '（整条移除）'}")

    if args.dry_run:
        print("\n（dry-run，未写入任何文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
