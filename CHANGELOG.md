# 更新说明

## v1.2.1 - 2026-06-25

本版本重点修复法术数据中学派字段的污染问题，并补充前端防御逻辑。

### 修复

- 修复法术描述文本污染到学派字段的问题。
- 修复英文学派未翻译的问题，将 `abjuration`、`conjuration`、`divination`、`enchantment`、`evocation`、`illusion`、`necromancy`、`transmutation`、`universal` 统一转换为中文学派。
- 修复职业/环位等级文本误入学派字段的问题，并将等级文本移回等级字段。
- 扩展 `scripts/fix/repair_spell_school_level_leak.py`，覆盖根目录与分来源目录下的法术 JSON。
- 增强前端法术归一化逻辑，旧数据中学派字段混入描述、等级或英文学派时会在展示前清洗。

## v1.2 - 2026-06-22

本版本重点是项目结构整理、精简运行版、资料浏览前端和车卡器基础能力。

### 新增

- 新增精简运行入口 `run_lite.py`，可在不加载 RAG/向量索引依赖的情况下运行资料浏览和查询功能。
- 新增 `pf_rag/runtime/`，集中维护 lite 运行时、静态资源挂载和法术目录加载逻辑。
- 新增车卡器页面 `web/character.html`，支持本地角色卡草稿、基础信息、属性、职业、专长、法术、奇物、战斗数据和 JSON 导出。
- 新增角色卡 `derived` 计算层，当前可计算属性调整值、职业等级合计、基础 HP 修正、BAB/CMB/CMD、AC、三项豁免、技能缓存、法术 DC 缓存和负重占位信息。
- 新增中文角色卡结构文档 `docs/CHARACTER_SCHEMA_ZH.md`。
- 新增中文代码结构说明 `docs/CODE_OVERVIEW_ZH.md`。
- 新增脚本目录说明 `scripts/README.md` 和前端说明 `web/README.md`。

### 调整

- 将 API 拆分为 `api/routers/`、`api/dependencies.py` 和独立 service，降低 `api/main.py` 复杂度。
- 将前端资源整理到 `web/assets/css/` 与 `web/assets/js/`。
- 将 `scripts/` 按用途分为 `analysis/`、`backfill/`、`books/`、`config/`、`extract/`、`fix/`、`localize/`、`locate/`、`package/`。
- 将 RAG README 移到 `docs/README_RAG.md`。
- 打包脚本输出目录和 zip 文件名现在带版本号，例如 `PFSearcherLite_v1.2_portable.zip`。

### 修复

- 修正法术职业分类中“巫师/法师”的翻译归类问题。
- 为法术数据补充区域字段。
- 法术筛选新增学派分类，并将神话法术作为独立分类。
- 从职业资料加入角色卡时，现在可以指定加入等级，不再只能加入 1 级。

### 当前限制

- 车卡器的 BAB、职业豁免成长、技能职业项、法术 DC 规则表还未完整接入，目前保留结构和基础计算。
- lite 版本不包含 RAG 智能问答、向量索引构建、数据抽取清洗脚本和外部模型依赖。

## v1.1

- 提供 FastAPI + 前端静态页面 + BM25/向量混合检索 + LLM 生成的完整运行形态。
- 支持法术检索、关键词检索、职业/环位筛选和 RAG 问答降级逻辑。
