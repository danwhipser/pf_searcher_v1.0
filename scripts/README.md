# scripts 目录说明

`scripts/` 存放数据提取、清洗、回填、修复、审计、构建和打包脚本。这里的脚本不属于精简版 exe 的运行时内容，但属于项目数据流水线和维护工具。

## 运行方式

推荐从项目根目录运行：

```powershell
python scripts\build\build_index.py
python scripts\package\package_lite.py
```

迁移到子目录后，每个正式脚本都带有项目根目录启动兼容逻辑，可以直接用脚本路径运行。也可以使用模块方式：

```powershell
python -m scripts.build.build_index
python -m scripts.package.package_lite
```

## 分层结构

### `analysis/`

审计、统计和报告脚本。主要用于检查当前数据质量、覆盖率和前端解析风险。

- `analyze_feat_short_detail_causes.py`
- `audit_frontend_parse_risks.py`
- `feat_book_aon_coverage.py`
- `feat_book_report.py`
- `expand_aon_coverage_check.py`

### `backfill/`

专长详情和缺失字段回填脚本。这里的脚本通常会读取 CHM/AoN/HTML 页面，并写回 `result/feats/` 或生成回填报告。

- `backfill_feat_detail_*.py`
- `backfill_missing_frontend_feats_from_aon.py`
- `backfill_story_feat_fields.py`
- `backfill_uc_from_page_lookup.py`
- `augment_*.py`

### `books/`

来源书扩展和额外书籍抽取脚本。用于处理缺失书籍、Player Companion、特殊来源书和 AoN 补充来源。

- `extract_missing_books.py`
- `extract_more_books.py`
- `extract_special_books.py`
- `extract_player_companion_books.py`
- `extract_isg_isi_books.py`
- `fetch_player_companion_aon_supplements.py`
- `build_player_companion_source_manifest.py`

### `build/`

构建类脚本。用于从已有数据生成前端数据、职业层级、RAG 索引或评估结果。

- `build_index.py`
- `evaluate.py`
- `build_feats_frontend_json.py`
- `build_class_profile_hierarchy.py`

### `config/`

本地配置脚本。

- `configure_api_keys.py`

### `extract/`

基础数据提取脚本。包括法术、专长、职业、职业能力和奇物等结构化数据提取。

- `extract_spells*.py`
- `extract_feats_and_verify.py`
- `extract_classes.py`
- `extract_class_special_abilities.py`
- `extract_wondrous_items.py`
- `batch_extract_spell_html.py`
- `export_spell_tables.py`

### `fix/`

已知数据问题修复脚本。用于修正污染字段、来源错配、等级解析失败、职业变体异常和前端解析风险等。

- `fix_*.py`
- `repair_*.py`
- `auto_fix_source_spell_names.py`
- `apply_validated_feat_field_fixes.py`
- `patch_vigilante_archetypes.py`
- `upgrade_feat_detail_longform_priority.py`
- `enforce_feat_detail_minimum.py`

### `localize/`

中文名和本地化回填脚本。

- `fill_cn_names_from_chm.py`
- `localize_names_from_chm.py`
- `localize_remaining_feat_names_from_pages.py`

### `locate/`

定位类脚本。用于定位 CHM 章节、页面或法术索引对应关系。

- `locate_feat_chapters_and_validate.py`
- `locate_spells_from_index.py`

### `package/`

打包和分发脚本。

- `package_lite.py`：构建精简分发版。
- `package_portable.py`：构建完整便携版。

### `_scratch/`

临时分析脚本目录，不作为稳定入口。这里的脚本可能依赖历史文件名或一次性数据，不保证长期可运行。

## 命名规则

- `extract_*`：从 CHM、HTML、AoN 等来源提取结构化数据。
- `build_*`：从已有数据构建前端 JSON、索引或清单。
- `backfill_*` / `augment_*`：补充缺失字段或扩展覆盖范围。
- `fix_*` / `repair_*`：修复已发现的数据错误。
- `audit_*` / `analyze_*` / `*_report.py`：审计、分析和报告。
- `locate_*`：定位页面、章节或数据来源。
- `package_*`：打包和分发。

## 迁移说明

本次整理保留了原脚本文件名，只移动到分类目录。脚本间 import 已改为新的包路径，例如：

```python
from scripts.extract.extract_feats_and_verify import normalize_key
from scripts.backfill.backfill_feat_detail_unified import clean_text
```

如果新增脚本需要复用旧逻辑，优先按目录职责放置，并使用 `scripts.<分类>.<模块>` 的导入路径。
