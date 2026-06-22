# api 目录说明

`api/` 是完整 RAG 版本的 FastAPI 后端。精简分发版不直接使用这里的 RAG 路由，而是使用 `pf_rag/runtime/lite_app.py`。

## 结构

```text
api/
  main.py                # FastAPI app 组装，只注册路由
  dependencies.py        # 完整版后端依赖：Retriever / Generator 单例
  config.py              # 配置、数据源发现、环境变量
  models.py              # Pydantic 请求/响应模型
  routers/
    health.py            # /api/health
    spells.py            # /api/spell-sources, /api/spells/keyword
    rag.py               # /api/rag/search, /api/rag/ask
  services/
    data_loader.py       # 法术 JSON -> SpellRecord
    spell_sources.py     # 法术数据源列表和关键词查询
    indexer.py           # Chroma / BM25 索引构建
    retriever.py         # BM25 + Vector + RRF 混合检索
    generator.py         # LLM 回答生成与降级
    embedding_client.py  # Embedding API / 本地模型适配
  utils/
    text_utils.py        # 文本清洗、等级解析、字段污染修复
```

## 修改建议

- 新增接口：优先放到 `routers/`，不要继续堆到 `main.py`。
- 新增业务逻辑：优先放到 `services/`。
- 需要共享的 FastAPI 依赖或单例：放到 `dependencies.py`。
- 只和文本清洗、字段解析有关的纯函数：放到 `utils/`。
- `main.py` 应保持很薄，只负责 app 元数据和路由注册。

