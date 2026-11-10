# Agent 接入

## MCP（stdio）

在 DTK 仓库根目录启动：

```powershell
python -m dtk mcp --db data/dtk.db
```

本地 MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "dtk": {
      "command": "python",
      "args": ["-m", "dtk", "mcp", "--db", "E:/code/DTK/data/dtk.db"]
    }
  }
}
```

可选：

- `--intent`：额外用本地 LLM 拒绝非视觉/无意义输入；需要 LM Studio。
- `--no-vectors`：仅精确名、Alias 和 FTS，不加载向量索引。
- `--db`：指定 Derived Store。

MCP 是标准 stdio JSON-RPC，支持 `initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`。

## 工具

### `search_tags`

输入：

```json
{"query": "角色直视观众", "limit": 10}
```

返回包含 `query`、`matched_by`、`results` 的结构化内容。每个候选至少含 `tag`、`tag_id`、`type`、`post_count`、`rank` 和 `matched_by`。语义结果另含 `match_score`/`vector_score`；Alias 歧义返回多个候选。

明确用中英文逗号、顿号或分号分隔的多个概念返回 `groups`，每组独立检索。

### `get_tags`

输入：

```json
{"names": ["looking_at_viewer", "black_footwear"]}
```

返回 `tags` 数组。每项包含官方 Wiki、Alias，以及人工发布后存在的 `research` 定义、语义文档、证据和发布时间。

不存在的名字在该位置返回 `null`，调用方应处理。

## REST（本地 Web 服务）

```powershell
python -m dtk serve --db data/dtk.db --port 8765
```

- `GET /api/search?query=...&limit=10`
- `GET /api/tag?name=...`
- `GET /health`

默认绑定 `127.0.0.1`。若显式 `--intent`，REST 查询同样启用意图门。

## 权限

Agent 接口只读。Research Agent 的搜索、研究、发布命令不通过 MCP 暴露；普通 Agent 查询不会写 Derived Store 或触发研究任务。
