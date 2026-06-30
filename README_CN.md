# 🌲 ZhipuGLM Hunter

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/GitHub%20sources-5-green?style=flat-square" alt="Sources">
  <img src="https://img.shields.io/badge/Queries-258+-red?style=flat-square" alt="Queries">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

> **在公开代码中查找泄露的智谱 AI（Zhipu / GLM）API 密钥，确认哪些仍然有效，可选检查 Coding Plan 配额，并通过负责任披露在受影响仓库开 Issue 提醒所有者轮换密钥。**

这是一款**防御性 / 负责任披露**工具。其目标是在攻击者耗尽密钥额度**之前**，将警告送达泄露者本人。余额/配额检查仅用于**本地研判**——披露 Issue 中从不包含账单详情。

灵感来自 [DarkForest-Hunter](https://github.com/chu0119/DarkForest-Hunter) 及其[自动披露设计](../DarkForest-Hunter-OpenAI/docs/superpowers/specs/2026-06-25-auto-disclosure-design.md)。

---

## 工作原理

```
搜索 GitHub（代码 · 提交 · Issue/PR · 可选 Gist/Events）
   → 提取智谱格式密钥  → 分组 & 去重
   → 验证密钥（/models → Coding Plan 配额回退探测）
   → [可选] 每个受影响仓库开一个脱敏、去重的披露 Issue
   → 导出结果到 results/
```

验证流程依次尝试**存活检测**（`/models`），以及面向国内/国际监控端点的 **Coding Plan 配额**探测。智谱 API **不提供**按量付费现金余额查询。若仅需存活验证（向仓库发披露 Issue 时推荐），请使用 `--no-balance`。无需重新扫描 GitHub，即可对已有结果重新验证：

```bash
.venv/bin/python verify_balances.py results/zhipu_keys_result.json --valid-only
```

如需确认密钥能否**实际消费**（会消耗约 1 token 配额），可使用独立的 spend probe 工具（扫描流程中不会自动运行）：

```bash
.venv/bin/python probe_spend.py results/zhipu_keys_result.json --valid-only --limit 5
```

## 功能概览

| 能力 | 状态 |
|---|---|
| GitHub **代码**搜索 | ✅ |
| GitHub **提交**搜索（消息 + patch） | ✅ |
| GitHub **Issue / PR**搜索 | ✅ |
| GitHub **Gist** 搜索（可选：`--sources …,github_gist`） | ✅ |
| GitHub **Events** PushEvent 监控（可选：`--sources github_events` 或 `zhipu_key_scanner.py --monitor`） | ✅ |
| 针对智谱 API 的存活验证 | ✅ |
| Coding Plan 配额检查（可用 `--no-balance` 关闭） | ✅ |
| 独立余额/配额复检（`verify_balances.py`） | ✅ |
| 消费探测（`probe_spend.py`，手动研判专用） | ✅ |
| 统一 CLI（`zhipu_key_scanner.py`）与命令生成器（`cmd_generator.html`） | ✅ |
| 高产出 / 高吞吐扫描（`expanded_scan.py`、`max_scan.py`） | ✅ |
| **负责任披露**（GitHub Issue、脱敏密钥、默认关闭、默认 dry-run） | ✅ |
| 持续扫描（`marathon_scan.py`） | ✅ |
| JSON / CSV / Markdown 导出 | ✅ |
| 非 GitHub 平台（GitLab、PyPI、npm 等） | ⏸️ 需先有可通知所有者的渠道 — 见 `PLAN.md` |

默认三个 GitHub 来源均**绑定仓库**，因此发现的 live 密钥可在同一仓库开 Issue 披露。可选来源 `github_gist`（伪仓库 `{owner}/gist`）与 `github_events`（公共事件流）通过 `--sources` 启用。尚无所有者通知路径的平台有意不扫描。

## 智谱 API 密钥格式

```
[a-f0-9]{32}\.[A-Za-z0-9]{8,64}
```

示例：`a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp`

## 快速开始

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

# 可选：登录 GitHub 以获得更高搜索限额 + 发布披露 Issue
gh auth login

# 仅检测（不披露）
.venv/bin/python quick_batch.py
.venv/bin/python deep_scan.py --hours 1 --pages 2
.venv/bin/python expanded_scan.py --hours 2 --extra-sources github_gist
.venv/bin/python zhipu_key_scanner.py --cmd-gen   # 在浏览器打开命令生成器

# 仅存活验证的披露运行（不探测配额/余额）
.venv/bin/python deep_scan.py --hours 1 --disclose --no-balance

# 对 results/ 中已有密钥重新检查配额
.venv/bin/python verify_balances.py results/zhipu_keys_result.json --valid-only

# 手动消费探测（会消耗少量配额，请限制 --limit）
.venv/bin/python probe_spend.py results/zhipu_keys_result.json --valid-only --limit 5

# 检测 + 预览披露 Issue，不实际发布（dry-run）
.venv/bin/python deep_scan.py --hours 1 --disclose

# 检测 + 实际通知 live 密钥的所有者
.venv/bin/python deep_scan.py --hours 1 --disclose-send --disclose-max-repo-age-days 365
```

完整参数、来源说明及披露流程见 [`USAGE.md`](USAGE.md)（英文）。

结果写入 `results/`（`zhipu_keys_result.json` / `.csv` / `.md`），披露台账位于 `results/disclosed.json`。这些文件可能含敏感发现，除 `results/.gitkeep` 外均已 gitignore。**JSON 与 CSV 含完整密钥**（`key` 列为原始值）；Markdown 摘要与披露 Issue 为脱敏/掩码显示。请将 `results/*.json` 与 `results/*.csv` 视为机密材料。

## 合规使用

仅用于**经授权的安全研究与负责任披露**。

- 切勿使用发现的密钥消费额度或调用推理接口（`probe_spend.py` 仅用于经授权的本地研判，且会消耗约 1 token）。
- 余额/配额检查仅用于**本地研判**；披露 Issue 从不包含账单数据。
- 披露**默认关闭**且**默认 dry-run**；实际发布需 `--disclose-send`。
- 披露 Issue 按仓库去重并限速，避免骚扰维护者。
- 若发现**您自己的**密钥，请立即在 <https://open.bigmodel.cn/usercenter/apikeys> 轮换。

## 许可证

MIT — 见 [LICENSE](LICENSE)。

<p align="center"><sub>🌲 在猎物被叼走之前，先联系到主人。</sub></p>
