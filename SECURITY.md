# 安全与数据处理 / Security & Data Handling

## 这个工具会碰到什么数据

| 数据 | 存在哪 | 会不会离开这台机器 |
|---|---|---|
| 岗位信息（岗位名 / JD / 链接） | `~/.cn-job-radar/data/` | 否。直接从企业公开接口读取 |
| 你的求职画像（`profile.yaml`） | 同上 | **否**。只在本地和 JD 做字符串匹配 |
| 公司池 / 机构池 | 同上 | 否 |
| 日报（`reports/`） | 同上 | 否 |
| 门户健康度 | `data/portal_health.json` | 否 |

**没有账号体系、没有遥测、没有任何形式的上传。** 唯一的出网请求是读取企业公开的招聘接口。

## 抓取边界（代码里就是这么实现的）

- **只读公开的岗位信息。** 不碰简历、不投递、不自动打招呼。
- **不登录任何站点。** 飞书招聘 / Moka / 北森用的都是企业自己公开的门户接口。
- **遇到人机验证或登录墙就停**，不绕、不用代理池、不伪装指纹。
- **同站限速**（默认 0.3 秒），429 按对方要求退避。

## 什么情况下你的信息会离开本机

只有一种：**你自己**把 `data/raw/*.json` 或 `config/profile.yaml` 提交到公开仓库或发给别人。

- `config/profile.yaml` **永远不要分享**。它包含你的目标城市、加分词、缺口词，组合起来能反推出你的履历。
- `data/raw/*.json` 是你从 BOSS 直聘 / 猎聘抓来的原始岗位数据，可能带平台水印或你的账号痕迹，分享前先检查。

## 分享公司池之前

如果你想把公司池（`config/companies.yaml`）开源或给同事，用自带的清洗工具：

```bash
python tools/export_seed.py --src ~/.cn-job-radar/config --out ./seed --dry-run
```

`--dry-run` 会先打印它打算改的每一处，**确认无误再去掉 `--dry-run`**。它会剔除：

- 任何含「你 / 您 / 我」的句子片段（这类句子往往是写给某个具体人的建议）
- 学历、简历、职业规划、期望薪资相关的表述

> ⚠️ 自动清洗**不是万无一失的**。它按句法切分，理解不了语义。提交前请自己通读一遍 diff。
> 公司名、融资数据、投资方、招聘门户域名本身是公开信息，保留没有问题。

## 报告安全问题

如果你发现：

- 本工具**上传**了任何用户数据（不应该发生）
- 本工具绕过了验证码 / 登录墙 / robots.txt
- 种子池或文档里混入了**真实的个人信息**
- 任何其他会让你在使用本工具后处于风险中的问题

请开一个 [issue](https://github.com/WEIWEI0614/cn-job-radar/issues)，如果内容敏感，标题标注 `[security]` 并只描述问题类型，细节私下沟通。

## 免责

本工具读取的是企业**主动公开**的岗位信息。请遵守你所在地区法律与目标网站的服务条款。
"公开可见"不等于"可以大规模聚合后重新发布"。

---

# Security & Data Handling (English)

**Nothing leaves your machine.** There is no account system, no telemetry, and no upload path.
The only outbound requests read public job-posting endpoints.

- Reads **public postings only** — no résumés, no applications, no auto-messaging.
- **Never logs in anywhere.**
- **Stops on CAPTCHA or a login wall** — no bypassing, no proxy pools, no fingerprint spoofing.
- Rate-limited per host (0.3s default), backs off on 429.

**The one way your data can leak is if you share it.** Never publish `config/profile.yaml` —
your target cities, strength keywords and gap keywords together can reconstruct your résumé.

Before publishing a company pool, run `tools/export_seed.py` (use `--dry-run` first). It strips
sentences containing 你/您/我 and anything about education, résumés, career plans or salary
expectations. It is **not** foolproof — read the diff yourself before committing.

To report a security issue, open an issue tagged `[security]`.
