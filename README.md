# 🌲 ZhipuGLM Hunter

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/GitHub%20sources-3-green?style=flat-square" alt="Sources">
  <img src="https://img.shields.io/badge/Queries-145+-red?style=flat-square" alt="Queries">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

> **Find exposed Zhipu AI (智谱 / GLM) API keys in public code, confirm which are still live, and open a responsible-disclosure issue telling the owner to rotate.**

This is a **defensive / responsible-disclosure** tool. It exists to get a warning to the
person who leaked a key **before** an attacker drains it. It does **not** read account balances,
spend credits, or rank keys by value.

Inspired by [DarkForest-Hunter](https://github.com/chu0119/DarkForest-Hunter) and its
[auto-disclosure design](../DarkForest-Hunter-OpenAI/docs/superpowers/specs/2026-06-25-auto-disclosure-design.md).

---

## How it works

```
search GitHub (code · commits · issues)
   → extract Zhipu-format keys  → group & dedup
   → confirm LIVENESS (one read-only /models call, never /user/balance)
   → [optional] open one masked, deduped disclosure issue per affected repo
   → export findings to results/
```

Verification is a **liveness check only**: it authenticates just enough to tell whether a key is
active (so a dead key or false positive doesn't bother a repo owner). It deliberately never
inspects balance, billing, or credit — see the note in `scanner_engine._verify_one`.

## What's built

| Capability | Status |
|---|---|
| GitHub **Code** search | ✅ |
| GitHub **Commits** search (messages + patches) | ✅ |
| GitHub **Issues / PRs** search | ✅ |
| Liveness validation against Zhipu API | ✅ |
| **Responsible disclosure** (GitHub issue, masked key, off-by-default, dry-run) | ✅ |
| Continuous (`marathon_scan.py`) scanning | ✅ |
| JSON / CSV / Markdown export | ✅ |
| Balance / billing inspection | 🚫 intentionally removed (no disclosure purpose) |
| Non-GitHub platforms (GitLab, PyPI, npm, …) | ⏸️ need a disclosure channel first — see `PLAN.md` |

All three GitHub sources are **repo-bound**, so any live key they find can be disclosed by opening
an issue on the same repo. Platforms with no owner-notification path are intentionally not scanned.

## Zhipu API key format

```
[a-f0-9]{32}\.[A-Za-z0-9]{8,64}
```

Example: `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp`

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

# Optional: authenticate GitHub for higher search limits + disclosure posting
gh auth login

# Detect only (no disclosure)
.venv/bin/python quick_batch.py
.venv/bin/python deep_scan.py --hours 1 --pages 2

# Detect + preview disclosure issues WITHOUT posting (dry-run)
.venv/bin/python deep_scan.py --hours 1 --disclose

# Detect + actually notify owners of live keys
.venv/bin/python deep_scan.py --hours 1 --disclose-send --disclose-max-repo-age-days 365
```

See [`USAGE.md`](USAGE.md) for all flags, sources, and the disclosure workflow.

Results are written under `results/` (`zhipu_keys_result.json/.csv/.md`) and a disclosure ledger at
`results/disclosed.json`. These may contain sensitive findings and are git-ignored except
`results/.gitkeep`. **Full keys appear only in the local JSON**; CSV/MD and disclosure issues are redacted/masked.

## Ethical use

For **authorized security research and responsible disclosure only**.

- Never use a discovered key to access an account — not even to read its balance.
- Disclosure is **off by default** and **dry-run by default**; real posting needs `--disclose-send`.
- Disclosure issues are deduped per repo and rate-limited so you don't spam maintainers.
- If one of *your* keys turns up, rotate it immediately at <https://open.bigmodel.cn/usercenter/apikeys>.

## License

MIT — see [LICENSE](LICENSE).

<p align="center"><sub>🌲 Reach the owner before the prey gets taken.</sub></p>
