# web 目录说明

`web/` 是前端静态资源目录，由 `run_web.py` 和 `run_lite.py` 挂载到 `/web/`。

## 页面

```text
web/
  index.html      # 总入口，iframe 嵌入各资料页
  spells.html     # 法术检索 / RAG 页面
  feats.html      # 专长检索页面
  classes.html    # 职业资料页面
  items.html      # 奇物资料页面
  character.html  # 自动车卡页面
```

这些 HTML 保留在 `web/` 根层，是为了保持 `/web/spells.html` 这类旧访问路径不变。

## 静态资源

```text
web/assets/
  css/
    index.css      # 总入口样式
    spells.css     # 法术页样式
    browser.css    # 专长/职业/奇物通用浏览器样式
    character.css  # 自动车卡样式
  js/
    index.js         # 总入口页签切换
    spell-rag.js     # 法术检索和 RAG 主逻辑
    spells-simple.js # 旧的简化法术浏览脚本，当前未接入页面
    feats.js         # 专长页逻辑
    classes.js       # 职业页逻辑
    items.js         # 奇物页逻辑
    character.js     # 自动车卡逻辑
```

## 数据依赖

- 法术页通过 `/api/spell-sources` 和 `/api/spells/keyword` 获取后端数据。
- 专长页读取 `/result/feats/feats-frontend.json`。
- 职业页读取 `/result/classes/classes-extracted.json`。
- 奇物页读取 `/result/items/wondrous-items.json`。
- 自动车卡页会读取职业、专长、法术、奇物数据，并使用浏览器本地存储保存角色草稿。

## 修改建议

- 新增页面时，HTML 放在 `web/` 根层，CSS/JS 放到 `web/assets/`。
- 不要再新增 `script.js` 这类含糊命名，脚本名应对应页面或功能。
- 保持数据路径以 `/result/...` 或 `/api/...` 开头，避免 iframe 相对路径混乱。

