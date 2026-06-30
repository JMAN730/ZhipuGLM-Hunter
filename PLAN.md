# 🌲 ZhipuGLM Hunter — Implementation Plan

> **"The universe is a dark forest. Every civilization is an armed hunter."**
> — Liu Cixin, *The Dark Forest*

A scanner that hunts down exposed **Zhipu AI (智谱 / GLM)** API keys across GitHub (code, commits, issues) — validates liveness, enriches Coding Plan keys with quota, optionally discloses to repo owners, and exports findings.

**Inspired by:** [DarkForest-Hunter](https://github.com/chu0119/DarkForest-Hunter) (DeepSeek key scanner)

**Status (2026-06-27):** Core MVP complete — five GitHub scanners (code, commits, issues, optional gist/events), liveness + Coding Plan quota verification, optional responsible disclosure, JSON/CSV/Markdown export, and multiple scan entry points. Non-GitHub platforms (GitLab, PyPI, npm, etc.) are **deferred** — no repo-bound disclosure path per project policy.

---

## 🎯 Key Differences from DarkForest-Hunter

| Aspect | DarkForest-Hunter (DeepSeek) | ZhipuGLM Hunter |
|--------|------------------------------|-----------------|
| **Key prefix** | `sk-[a-zA-Z0-9]{32,64}` | `[a-f0-9]{32}\.[A-Za-z0-9]+` |
| **API base** | `https://api.deepseek.com` | `https://open.bigmodel.cn/api/paas/v4` |
| **Validation endpoint** | `GET /user/balance` | `GET /models` (liveness), then `GET /api/monitor/usage/quota/limit` (Coding Plan) |
| **Balance check** | Yes (native endpoint) | Coding Plan quota via monitor endpoint; pay-as-you-go keys → `balance_unavailable` (`/user/balance` returns 404 on Zhipu) |
| **Verify log output** | `USD/CNY X.XXXX (≈$… / ¥…)` | `quota … tokens remaining` · `valid (pay-as-you-go, balance N/A)` · or `invalid_key` |
| **Search queries** | `deepseek sk-` patterns | `zhipu`, `bigmodel`, `chatglm`, `glm-4` patterns |
| **Primary currency** | USD/CNY | CNY (native) |

---

## 📐 Architecture

```
ZhipuGLM-Hunter/
├── scanner_engine.py          # Core engine (search + verify + quota + save) ✅
├── disclosure.py              # Responsible-disclosure GitHub issues ✅
├── scanners/                  # Platform scanners
│   ├── __init__.py            ✅
│   ├── base.py                # Key extraction + helpers ✅
│   ├── github_code.py         # GitHub Code Search ✅
│   ├── github_commits.py      # GitHub Commit history ✅
│   ├── github_issues.py       # GitHub Issues/PRs ✅
│   ├── github_gist.py         # GitHub Gist (optional source) ✅
│   ├── github_events.py       # Real-time PushEvent stream (optional) ✅
│   ├── gitlab.py              # GitLab ⏸️ deferred (no disclosure path)
│   ├── gitee.py               # Gitee (码云) ⏸️ deferred
│   ├── huggingface.py         # HuggingFace Models/Datasets/Spaces ⏸️ deferred
│   ├── pypi.py                # PyPI packages ⏸️ deferred
│   ├── npm_registry.py        # npm registry ⏸️ deferred
│   ├── stackoverflow.py       # Stack Overflow ⏸️ deferred
│   ├── docker.py              # Docker Hub ⏸️ deferred
│   ├── commoncrawl.py         # Common Crawl archives ⏸️ deferred
│   └── wayback.py             # Wayback Machine ⏸️ deferred
├── tests/                     # Network-free unit tests ✅ (67 passing)
│   ├── test_engine_pure.py
│   ├── test_base_helpers.py
│   ├── test_engine_sources.py
│   ├── test_scanners_github.py
│   ├── test_disclosure.py
│   ├── test_verify_balances.py
│   └── test_probe_spend.py
├── queries_v4.txt             # Search query library (~258 patterns) ✅
├── quick_batch.py             # Quick bounded test scan ✅
├── deep_scan.py               # Configurable deep scan ✅
├── ultimate_scan.py           # Long-running MVP scan ✅
├── marathon_scan.py           # Continuous cyclic scan ✅
├── expanded_scan.py           # High-yield queries + optional gist ✅
├── max_scan.py                # Full library, deep pagination ✅
├── zhipu_key_scanner.py       # Unified CLI wrapper ✅
├── verify_balances.py         # Re-check saved keys (liveness + quota) ✅
├── probe_spend.py             # 1-token spend probe (manual triage) ✅
├── pyproject.toml             # Package + dev deps ✅
├── requirements.txt           # Runtime deps ✅
├── results/                   # Output directory
│   └── .gitkeep
├── README.md                  # English readme ✅
├── USAGE.md                   # Detailed usage guide ✅
├── README_CN.md               # Chinese readme ✅
├── cmd_generator.html         # Scan command GUI ✅
├── LICENSE                    # MIT ✅
├── .gitignore                 ✅
└── PLAN.md                    # This file
```

---

## 🔑 Zhipu API Key Format

### Pattern

```
[a-f0-9]{32}\.[A-Za-z0-9]{8,64}
```

- **Part 1 (before dot):** Exactly 32 lowercase hex characters (the "key ID")
- **Separator:** A literal dot `.`
- **Part 2 (after dot):** 8-64 mixed-case alphanumeric characters (the "secret")

### Examples

```
a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp
```

### Common Variable Names (for search patterns)

```
ZHIPU_API_KEY
ZHIPUAI_API_KEY
GLM_API_KEY
BIGMODEL_API_KEY
CHATGLM_API_KEY
zhipu_api_key
zhipuai_api_key
api_key_zhipu
```

### Common API Hosts (for search patterns)

```
open.bigmodel.cn
bigmodel.cn
open.bigmodel.cn/api/paas/v4
```

---

## 🔍 Search Query Library (Initial Set)

### 🔥 Tier 1 — High Yield (config/env files)

| # | Query | Target |
|---|-------|--------|
| 1 | `zhipu api key filename:env` | .env files |
| 2 | `zhipu api key filename:env.local` | Local env |
| 3 | `zhipu api key filename:env.production` | Production env |
| 4 | `zhipu api key filename:env.example` | Example env |
| 5 | `ZHIPU_API_KEY filename:env` | Variable name |
| 6 | `bigmodel api key filename:env` | BigModel ref |
| 7 | `open.bigmodel.cn filename:env` | API host in env |
| 8 | `zhipu filename:credentials` | Credentials files |
| 9 | `zhipu filename:secrets` | Secrets files |
| 10 | `zhipu filename:config` | Config files |
| 11 | `zhipu filename:yml` | YAML config |
| 12 | `zhipu filename:yaml` | YAML config |
| 13 | `zhipu filename:json` | JSON config |
| 14 | `zhipu filename:toml` | TOML config |
| 15 | `zhipu filename:ini` | INI config |
| 16 | `zhipu filename:properties` | Java properties |

### 🔥 Tier 1 — Python Code

| # | Query | Target |
|---|-------|--------|
| 17 | `zhipu api key filename:py` | Python files |
| 18 | `ZhipuAI api_key filename:py` | SDK init |
| 19 | `zhipuai filename:py NOT env` | ZhipuAI SDK |
| 20 | `bigmodel filename:py` | BigModel SDK |
| 21 | `chatglm api key filename:py` | ChatGLM ref |
| 22 | `glm-4 api key filename:py` | GLM-4 model ref |
| 23 | `open.bigmodel.cn filename:py` | API host in code |
| 24 | `zhipu client filename:py` | Client usage |
| 25 | `zhipu def filename:py` | Function defs |
| 26 | `zhipu requests filename:py` | HTTP requests |

### 🔥 Tier 1 — JavaScript/TypeScript

| # | Query | Target |
|---|-------|--------|
| 27 | `zhipu api key filename:js` | JavaScript |
| 28 | `zhipu api key filename:ts` | TypeScript |
| 29 | `zhipu api key filename:jsx` | React JSX |
| 30 | `zhipu api key filename:tsx` | React TSX |
| 31 | `zhipu api key filename:vue` | Vue |
| 32 | `zhipu api key filename:mjs` | ES Modules |
| 33 | `zhipu api key filename:cjs` | CommonJS |
| 34 | `open.bigmodel.cn filename:js` | API host in JS |
| 35 | `open.bigmodel.cn filename:ts` | API host in TS |

### 🔥 Tier 2 — Other Languages

| # | Query | Target |
|---|-------|--------|
| 36 | `zhipu api key filename:java` | Java |
| 37 | `zhipu api key filename:kt` | Kotlin |
| 38 | `zhipu api key filename:go` | Go |
| 39 | `zhipu api key filename:rs` | Rust |
| 40 | `zhipu api key filename:php` | PHP |
| 41 | `zhipu api key filename:rb` | Ruby |
| 42 | `zhipu api key filename:swift` | Swift |
| 43 | `zhipu api key filename:sh` | Shell scripts |
| 44 | `zhipu api key filename:bash` | Bash |

### 🔥 Tier 2 — Docker & CI/CD

| # | Query | Target |
|---|-------|--------|
| 45 | `zhipu filename:Dockerfile` | Dockerfiles |
| 46 | `zhipu filename:docker-compose.yml` | Docker Compose |
| 47 | `zhipu filename:.github/workflows` | GitHub Actions |
| 48 | `zhipu filename:gitlab-ci.yml` | GitLab CI |
| 49 | `zhipu filename:Jenkinsfile` | Jenkins |
| 50 | `zhipu filename:Makefile` | Makefiles |

### 🔥 Tier 2 — Notebooks & Docs

| # | Query | Target |
|---|-------|--------|
| 51 | `zhipu filename:ipynb` | Jupyter notebooks |
| 52 | `zhipu filename:Rmd` | R Markdown |
| 53 | `zhipu filename:md` | Markdown docs |
| 54 | `zhipu filename:rst` | RST docs |

### 🔥 Tier 3 — Framework Integrations

| # | Query | Target |
|---|-------|--------|
| 55 | `langchain zhipu api_key` | LangChain |
| 56 | `dify zhipu api_key` | Dify |
| 57 | `fastgpt zhipu api_key` | FastGPT |
| 58 | `litellm zhipu api_key` | LiteLLM |
| 59 | `autogen zhipu api_key` | AutoGen |
| 60 | `crewai zhipu api_key` | CrewAI |
| 61 | `ragflow zhipu api_key` | RAGFlow |
| 62 | `oneapi zhipu` | One API |
| 63 | `lobechat zhipu` | LobeChat |
| 64 | `nextchat zhipu` | NextChat |
| 65 | `chatgpt-on-wechat zhipu` | ChatGPT-on-WeChat |

### 🔥 Tier 3 — Time-filtered (recent leaks)

| # | Query | Target |
|---|-------|--------|
| 66 | `zhipu api key pushed:>2025-01-01` | Recent pushes |
| 67 | `zhipu api key created:>2025-01-01` | Recent repos |

### 🔥 Tier 4 — Multi-platform

| # | Platform | Query |
|---|----------|-------|
| 68 | Gist | `zhipu api key` |
| 69 | Issues | `zhipu api key` |
| 70 | Commits | `zhipu api key` |
| 71 | GitLab | `zhipu api key` |
| 72 | Gitee | `智谱 api key` |
| 73 | HuggingFace | `zhipu` |
| 74 | PyPI | `zhipuai` |
| 75 | npm | `zhipu` |

---

## ✅ Validation & Quota

Verification is **liveness-first**, then optional **Coding Plan quota** enrichment when `check_balance` is enabled (default in scan scripts). Results are sorted **valid-first** (not by `balance_usd`) — the tool triages who to notify, not which credential is most valuable.

### Step 1: Liveness (Models List)

Every candidate key is authenticated with a cheap model-list probe:

```bash
GET https://open.bigmodel.cn/api/paas/v4/models
Authorization: Bearer {api_key}
```

**Responses:**
- `200` + model list → Valid key; proceed to quota probe (if enabled)
- `401` → Invalid key
- `402` → Insufficient balance
- `429` → Rate limited

**Engine behavior:**
- Invalid keys stop here with `reason: invalid_key` (etc.)
- Valid keys continue to Step 2 when balance/quota checks are enabled

### Step 2: Coding Plan Quota (Implemented)

For live keys, the engine probes the unofficial monitor endpoint across three bases (first `200` with parseable quota wins):

```bash
GET https://open.bigmodel.cn/api/monitor/usage/quota/limit
GET https://bigmodel.cn/api/monitor/usage/quota/limit
GET https://api.z.ai/api/monitor/usage/quota/limit
Authorization: Bearer {api_key}
```

**Responses:**
- `200` + `success: true` + `TOKENS_LIMIT` in `data.limits` → Valid Coding Plan key; parse remaining tokens / percent
- Other statuses or missing quota payload → Fall through to pay-as-you-go classification

**Engine behavior:**
- Logs: `verify a1b2… -> quota 1.2M tokens remaining (Coding Plan (…))`
- Export fields: `balance_kind: quota`, `primary_currency: TOKENS`, `provider_note`, `quota_plan`, `quota_remaining_pct`

### Pay-as-you-go Keys

When liveness succeeds but no Coding Plan quota is returned, the key is marked live with cash balance unavailable:

- `balance_unavailable: true`
- `provider_note: Pay-as-you-go (cash balance not exposed via API)`
- Logs: `verify a1b2… -> valid (pay-as-you-go, balance N/A)`
- Markdown export shows balance column as `N/A`

### Not Used: `/user/balance`

Zhipu’s `GET /user/balance` returns **404** — it is **not** called during scans. The `parse_zhipu_balance()` helper remains in `scanner_engine.py` for unit tests and backward compatibility only.

```bash
GET https://open.bigmodel.cn/api/paas/v4/user/balance   # 404 on Zhipu — do not use
Authorization: Bearer {api_key}
```

### Export Sensitivity

| Format | Key column |
|--------|------------|
| JSON | Full key (local-only; `results/` is gitignored) |
| CSV | Full key |
| Markdown | Redacted (`key_redacted` / `redact_key()`) |

Verify logs always use redacted keys regardless of export format.

### Optional: Re-verify Saved Keys

`verify_balances.py` re-runs liveness + quota against an existing `zhipu_keys_result.json` or a newline-delimited keys file. Pass `--no-balance` for liveness-only (`/models`).

### Spend Probe: `probe_spend.py` (Implemented)

Sends a **1-token** chat completion to test whether a key can actually consume quota/balance. This is the only probe that confirms spendability for pay-as-you-go keys (costs ~1 token per success).

```bash
.venv/bin/python probe_spend.py YOUR_API_KEY
.venv/bin/python probe_spend.py results/zhipu_keys_result.json --valid-only --limit 5
.venv/bin/python probe_spend.py --keys-file keys.txt --output results/spend_probe.json
```

```bash
POST https://open.bigmodel.cn/api/paas/v4/chat/completions
Authorization: Bearer {api_key}
Content-Type: application/json
Body: {"model":"glm-4-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1}
```

- `200` → Valid key + can spend (probe succeeded)
- `401` → Invalid key
- `402` → Live key but insufficient balance/quota
- `429` → Rate limited

Also tries `https://api.z.ai/api/paas/v4/chat/completions` as fallback. **Not run during scans** — use manually for triage only.

### Not Used (Legacy)

---

## 📋 Implementation Phases

### Phase 1: Core Engine (Day 1)

- [x] Fork/adapt `scanner_engine.py`
  - [x] Change `KEY_PATTERN` → Zhipu key regex
  - [x] Change API base → `https://open.bigmodel.cn/api/paas/v4`
  - [x] Rewrite `_verify_one()` → liveness-first (`GET /models`), then Coding Plan quota
  - [x] Implement `parse_zhipu_quota()` for monitor endpoint (open.bigmodel.cn, bigmodel.cn, api.z.ai)
  - [x] Keep `parse_zhipu_balance()` as test-only helper (`/user/balance` returns 404 on Zhipu)
  - [x] Replace all `BUILTIN_QUERIES` → Zhipu-specific queries
  - [x] Update currency handling (CNY native, USD/CNY conversion for legacy cash parse)
  - [x] Update output naming (`zhipu_keys_result.json` etc.)
  - [x] Update all references from "deepseek" → "zhipu"
  - [x] Sort results valid-first (not by `balance_usd`)
  - [x] Export: JSON/CSV full keys; Markdown redacted keys

### Phase 2: Scanners (Day 1-2)

- [x] Implement optional GitHub scanners: `github_gist`, `github_events` (opt-in via `--sources`)
- [x] Create `scanners/base.py` → Zhipu key pattern in `extract_keys()`
- [x] Implement `scanners/github_code.py`
- [x] Implement `scanners/github_commits.py`
- [x] Implement `scanners/github_issues.py`
- [ ] ~~Copy remaining scanners from DarkForest-Hunter (GitLab, HuggingFace, etc.)~~ **Deferred** — no repo-bound disclosure path; see README
- [ ] ~~Test each remaining scanner individually~~ N/A until non-GitHub platforms are in scope

### Phase 3: Search Queries (Day 2)

- [x] Create `queries_v4.txt` with Zhipu-specific queries (~258 patterns)
- [x] Expand to 200+ patterns
- [ ] Test query yield rates (operational tuning)
- [ ] Tune query ordering by yield (operational tuning)
- [ ] Add Chinese-language queries (GitHub supports Unicode search)

### Phase 4: Scan Scripts (Day 2)

- [x] Rewrite `ultimate_scan.py` for Zhipu
- [x] Rewrite `quick_batch.py`
- [x] Rewrite `deep_scan.py`
- [x] Rewrite `marathon_scan.py`
- [x] Add `verify_balances.py` (re-check saved JSON or keys file)
- [x] Add `probe_spend.py` (1-token spend probe, manual triage)
- [x] Rewrite `expanded_scan.py`
- [x] Rewrite `max_scan.py`
- [x] Rewrite `deepseek_key_scanner.py` → `zhipu_key_scanner.py`

### Phase 5: Documentation & Polish (Day 3)

- [x] Write `README.md` (English)
- [x] Write `USAGE.md`
- [x] Implement `disclosure.py` (responsible-disclosure GitHub issues; dry-run by default)
- [x] Write `README_CN.md` (Chinese)
- [x] Create `cmd_generator.html` GUI
- [x] Add `.gitignore`
- [x] Add `LICENSE` (MIT)
- [x] Add `tests/` with network-free coverage (67 tests passing)
- [x] Test GitHub multi-source scan wiring (unit tests; E2E optional)
- [ ] Tag v1.0.0 release (manual git tag when ready)

---

## 🚀 Quick Start

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest

# Authenticate GitHub for higher rate limits
gh auth login

# Quick bounded test scan (~15 minutes)
.venv/bin/python quick_batch.py

# Configurable deep scan
.venv/bin/python deep_scan.py --hours 3

# Long-running MVP scan
.venv/bin/python ultimate_scan.py

# Continuous cyclic scan (optional disclosure)
.venv/bin/python marathon_scan.py --interval-minutes 30 --disclose

# Re-check liveness + quota on saved results
.venv/bin/python verify_balances.py results/zhipu_keys_result.json
```

Results are written to `results/zhipu_keys_result.{json,csv,md}`. JSON and CSV contain **full keys**; Markdown redacts them. Quota and pay-as-you-go status appear in balance columns when available.

---

## ⚠️ Ethical Considerations

- This tool is for **authorized security research and auditing only**
- Do NOT use discovered keys for unauthorized access
- If you find your own key exposed, rotate it immediately on [open.bigmodel.cn](https://open.bigmodel.cn)
- Results containing actual API keys must be treated as **highly sensitive**
- Use `.gitignore` to exclude `results/` from version control

---

## 📊 Success Metrics

| Metric | Target | MVP Status |
|--------|--------|------------|
| Search patterns | 200+ | ✅ ~258 in `queries_v4.txt` |
| GitHub sources | 5 (code · commits · issues · gist · events) | ✅ 3 default + 2 optional |
| Other platforms | 14 (DarkForest parity) | ⏸️ Deferred — no disclosure channel |
| Verification | Liveness + quota enrichment | ✅ `GET /models` then Coding Plan quota probe |
| Cash balance via API | Nice-to-have | ❌ `/user/balance` returns 404; pay-as-you-go → `balance_unavailable` |
| Responsible disclosure | Optional per-repo issues | ✅ `disclosure.py` + `--disclose` / `--disclose-send` |
| Re-verify utility | Re-check saved keys | ✅ `verify_balances.py` |
| Unit tests | Network-free coverage | ✅ 67 tests passing |
| Result sort order | Valid-first triage | ✅ Not ranked by `balance_usd` |
| Export redaction | JSON/CSV full keys; MD redacted | ✅ JSON/CSV local-only; MD uses masked keys |
| Scan speed | 30-50 keys/minute (concurrent verify) | TBD |
| False positive rate | <5% (strict key regex) | TBD |
| Key format accuracy | Match all valid Zhipu key formats | ✅ Regex in `scanners/base.py` |

---

*Plan created: 2026-05-22 · Last updated: 2026-06-27 (Phase 2–5 complete; non-GitHub platforms deferred)*
