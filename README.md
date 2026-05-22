# 🌲 ZhipuGLM Hunter

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Scanners-14-green?style=flat-square" alt="Scanners">
  <img src="https://img.shields.io/badge/Queries-200+-red?style=flat-square" alt="Queries">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

<p align="center">
  <em>"The universe is a dark forest. Every civilization is an armed hunter."</em><br>
  <sub>— <strong>Liu Cixin</strong>, <em>The Dark Forest</em></sub>
</p>

---

> **Scan 14 platforms with 200+ search patterns to find exposed Zhipu AI (智谱 / GLM) API keys, validate them, and check their balance.**

Inspired by [DarkForest-Hunter](https://github.com/chu0119/DarkForest-Hunter) (DeepSeek key scanner).

---

## 🚧 Status: Planning Phase

This project is currently being built. See [`PLAN.md`](PLAN.md) for the full implementation plan.

### Why Zhipu GLM?

Zhipu AI (智谱) is one of China's leading AI companies, powering the **GLM** series of models (GLM-4, ChatGLM, CodeGeeX). Thousands of developers use Zhipu's API daily — and many accidentally hardcode their API keys in public repositories.

**There is currently NO tool scanning for exposed Zhipu API keys. We're building the first.**

---

## 🎯 What It Will Do

| Feature | Status |
|---------|--------|
| Search 14 platforms for exposed Zhipu API keys | 🔜 Planned |
| Validate keys against Zhipu API | 🔜 Planned |
| Check balance of valid keys | 🔜 Planned |
| Export results as JSON/CSV/Markdown | 🔜 Planned |
| Real-time GitHub PushEvent monitoring | 🔜 Planned |
| HTML GUI command generator | 🔜 Planned |

### Platforms to Scan

| Category | Sources |
|----------|---------|
| Code Hosting | GitHub Code, Gist, Issues, Commits, GitLab, Gitee |
| AI Platforms | HuggingFace (Models, Datasets, Spaces) |
| Package Registries | PyPI, npm |
| Developer Communities | Stack Overflow |
| Archives | Docker Hub, Wayback Machine, Common Crawl |
| Real-time | GitHub Events (PushEvent) |

---

## 🔑 Zhipu API Key Format

```
[a-f0-9]{32}\.[A-Za-z0-9]{8,64}
```

Example: `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6.AbCdEfGhIjKlMnOp`

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

<p align="center">
  🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲🌲<br>
  <em>"May the ethical hunters reach the prey first."</em>
</p>
