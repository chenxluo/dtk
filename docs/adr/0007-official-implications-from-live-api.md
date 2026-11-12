# ADR 0007: 官方 Implication 从本站实时 API 导入为加性表

## 状态

已接受（2026-11-12）

## 背景

Source Snapshot 没有 implication 数据；Danbooru 本站恢复后，
`/tag_implications.json` 可用（经系统代理）。官方 implication 是 Danbooru
社区维护的蕴含关系（如 `pantyshot -> panties`），对 Agent 推理和检索扩展
都有直接价值，此前因本站不可用而搁置。

## 决策

- 通过 `dtk/import-implications` 分页拉取 `status=active` 的官方
  Tag Implication，写入 Derived Store 的加性表 `tag_implications`。
- 导入幂等（`INSERT OR REPLACE`，按上游 id），可重复运行增量同步。
- meta 记录 `implications_fetched_at` / `implication_count` / 来源 URL。
- `get_tag` 附带 `implications: {implies, implied_by}`；无表或无关系时为
  `null`。MCP 与 REST 经 `get_tag` 自动获得该字段。
- `check-upstream` 对比本站最新帖与快照，量化快照落后程度；不做自动同步。

## 理由

- 官方 implication 与快照 Wiki 同源同权，是权威数据，不属研究产出，
  因此不需要 evidence/审核流程，但必须是加性表：Derived Store 可重建。
- 上游 id 作为主键使同步幂等且可对账。
- 快照与上游的差距（当前约 47 万帖）说明"检查"比"假装同步"诚实。

## 后果

- 重建 Derived Store（`python -m dtk import`）后需重跑
  `import-implications`，与 research 表一样属于加性数据。
- implication 语义是"打此标签应同时打目标标签"，不是别名；
  检索层暂不自动展开，交给 Agent 决定。
