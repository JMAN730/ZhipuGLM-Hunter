# 🌲 ZhipuGLM Hunter — Implementation Plan

> **"The universe is a dark forest. Every civilization is an armed hunter."**
> — Liu Cixin, *The Dark Forest*

A scanner that hunts down exposed **Zhipu AI (智谱 / GLM)** API keys across GitHub, GitLab, HuggingFace, PyPI, npm, Docker Hub, and more — validates them, and checks their balance.

**Inspired by:** [DarkForest-Hunter](https://github.com/chu0119/DarkForest-Hunter) (DeepSeek key scanner)

---

## 🎯 Key Differences from DarkForest-Hunter

| Aspect | DarkForest-Hunter (DeepSeek) | ZhipuGLM Hunter |
|--------|------------------------------|-----------------|
| **Key prefix** | `sk-[a-zA-Z0-9]{32,64}` | `[a-f0-9]{32}\.[A-Za-z0-9]+` |
| **API base** | `https://api.deepseek.com` | `https://open.bigmodel.cn/api/paas/v4` |
| **Validation endpoint** | `GET /user/balance` | `GET /v4/models` (auth check) |
| **Balance check** | Yes (native endpoint) | Yes (via `/v4/account` or billing) |
| **Search queries** | `deepseek sk-` patterns | `zhipu`, `bigmodel`, `chatglm`, `glm-4` patterns |
| **Primary currency** | USD/CNY | CNY (native) |

---

## 📐 Architecture

```
ZhipuGLM-Hunter/
├── scanner_engine.py          # Core engine (search + verify + balance + save)
├── scanners/                  # Platform scanners
│   ├── __init__.py
│   ├── base.py                # Base scanner class + key extraction
│   ├── github_code.py         # GitHub Code Search
│   ├── github_gist.py         # GitHub Gist
│   ├── github_issues.py       # GitHub Issues/PRs
│   ├── github_commits.py      # GitHub Commit history
│   ├── github_events.py       # Real-time PushEvent stream
│   ├── gitlab.py              # GitLab
│   ├── gitee.py               # Gitee (码云)
│   ├── huggingface.py         # HuggingFace Models/Datasets/Spaces
│   ├── pypi.py                # PyPI packages
│   ├── npm_registry.py        # npm registry
│   ├── stackoverflow.py       # Stack Overflow
│   ├── docker.py              # Docker Hub
│   ├── commoncrawl.py         # Common Crawl archives
│   └── wayback.py             # Wayback Machine
├── queries_v4.txt             # Search query library (~200+ patterns)
├── ultimate_scan.py           # Full 5-phase comprehensive scan
├── quick_batch.py             # Quick 15-min test scan
├── deep_scan.py               # Configurable deep scan
├── marathon_scan.py           # Long-running cyclic scan
├── results/                   # Output directory
│   └── .gitkeep
├── README.md                  # English readme
├── README_CN.md               # Chinese readme
├── USAGE.md                   # Detailed usage guide
├── LICENSE                    # MIT
├── .gitignore
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

## ✅ Validation Endpoint

### Primary: Models List (Auth Check)

```bash
GET https://open.bigmodel.cn/api/paas/v4/models
Authorization: {api_key}
```

**Responses:**
- `200` → Valid key (returns available models list)
- `401` → Invalid key
- `429` → Rate limited

### Secondary: Chat Completion (Smoke Test)

```bash
POST https://open.bigmodel.cn/api/paas/v4/chat/completions
Authorization: {api_key}
Content-Type: application/json
Body: {"model":"glm-4-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1}
```

- `200` → Valid key + working billing
- `401` → Invalid key
- `429/402` → Rate limited / insufficient balance

### Balance Check (if available)

Zhipu's billing API endpoint (to be confirmed):
```bash
GET https://open.bigmodel.cn/api/paas/v4/account
Authorization: {api_key}
```

---

## 📋 Implementation Phases

### Phase 1: Core Engine (Day 1)

- [ ] Fork/adapt `scanner_engine.py`
  - [ ] Change `KEY_PATTERN` → Zhipu key regex
  - [ ] Change `deepseek_api_base` → `https://open.bigmodel.cn/api/paas/v4`
  - [ ] Rewrite `_verify_one()` → Zhipu validation endpoint
  - [ ] Rewrite balance parsing → Zhipu balance response format
  - [ ] Replace all `BUILTIN_QUERIES` → Zhipu-specific queries
  - [ ] Update currency handling (CNY native)
  - [ ] Update output naming (`zhipu_keys_result.json` etc.)
  - [ ] Update all references from "deepseek" → "zhipu"

### Phase 2: Scanners (Day 1-2)

- [ ] Copy all 14 scanners from DarkForest-Hunter
- [ ] Update `scanners/base.py` → Zhipu key pattern in `extract_keys()`
- [ ] Update scanner search parameters
- [ ] Test each scanner individually

### Phase 3: Search Queries (Day 2)

- [ ] Create `queries_v4.txt` with 200+ Zhipu-specific queries
- [ ] Test query yield rates
- [ ] Tune query ordering by yield
- [ ] Add Chinese-language queries (GitHub supports Unicode search)

### Phase 4: Scan Scripts (Day 2)

- [ ] Rewrite `ultimate_scan.py` for Zhipu
- [ ] Rewrite `quick_batch.py`
- [ ] Rewrite `deep_scan.py`
- [ ] Rewrite `marathon_scan.py`
- [ ] Rewrite `expanded_scan.py`
- [ ] Rewrite `max_scan.py`
- [ ] Rewrite `deepseek_key_scanner.py` → `zhipu_key_scanner.py`

### Phase 5: Documentation & Polish (Day 3)

- [ ] Write `README.md` (English)
- [ ] Write `README_CN.md` (Chinese)
- [ ] Write `USAGE.md`
- [ ] Create `cmd_generator.html` GUI
- [ ] Add `.gitignore`
- [ ] Add `LICENSE` (MIT)
- [ ] Test full scan end-to-end
- [ ] Tag v1.0.0 release

---

## 🚀 Quick Start (Target)

```bash
pip install aiohttp requests

# Authenticate GitHub for higher rate limits
gh auth login

# Quick test (15 minutes)
python quick_batch.py

# Full scan (10-14 hours)
python ultimate_scan.py

# Deep scan (configurable)
python deep_scan.py --hours 3
```

---

## ⚠️ Ethical Considerations

- This tool is for **authorized security research and auditing only**
- Do NOT use discovered keys for unauthorized access
- If you find your own key exposed, rotate it immediately on [open.bigmodel.cn](https://open.bigmodel.cn)
- Results containing actual API keys must be treated as **highly sensitive**
- Use `.gitignore` to exclude `results/` from version control

---

## 📊 Success Metrics

| Metric | Target |
|--------|--------|
| Search patterns | 200+ |
| Platforms scanned | 14 |
| Scan speed | 30-50 keys/minute (concurrent verify) |
| False positive rate | <5% (strict key regex) |
| Key format accuracy | Match all valid Zhipu key formats |

---

*Plan created: 2026-05-22*
