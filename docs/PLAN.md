# 施工计划

## 目标

建设以 Danbooru Tag 实体为中心的知识与检索系统：

1. 人类可用中文自然语言查询 Canonical Tag、释义和关系。
2. 写 tag 的 Agent 可通过稳定接口获得真实、可追溯的标签知识。
3. Research Agent 只作为离线知识生产者，不阻塞普通查询。

## 已核实的起点

| 项目 | 数值 / 状态 | 影响 |
|---|---:|---|
| 源库 | `G:/标注任务/metadata/core_data_new_v3.db`，约 58.1 GB | 作为只读 Source Snapshot |
| 快照时间 | 最新帖子 2026-08-23 | 所有结果记录快照版本 |
| Tag | 1,082,991 | M1 全量导入 |
| Wiki | 335,769 | M1 导入并建 FTS |
| Alias 行 | 616,836；歧义 Alias 6,308 | 解析必须返回多候选 |
| General 缺 Wiki | 21,070，其中 17,092 图量 <10 | 不做全量研究 |
| General/Meta 研究候选 | 图量 ≥100 共 1,254；≥1000 共 375 | 默认研究范围 |
| 中文 Wiki | 44,681，角色占 44,645，General 仅 33 | 中文视觉查询依靠跨语言向量与查询改写 |
| 本地模型 | LM Studio 6001：`qwen3.8-27b`、Qwen3 Embedding 4B/8B | 默认本地模型 |
| 缺失官方数据 | Implication、部分状态与更新时间 | 等本站恢复后增量补齐 |

## 当前状态（2026-11-10）

- M0、M1 完成：Git 基线、规范、Derived Store、CLI、46 条人工种子 Benchmark 已建立。
- 真实快照导入对账：Tag 1,082,991 / Wiki 335,769 / Alias 616,836；导入耗时约 8 秒，派生库约 320 MB。
- M1 FTS 基线：hit@1 0.565、hit@5 0.609、hit@10 0.652、MRR 0.589。
- 已知边界：中文自然语言与模糊英文排序失败，属于 M2 向量检索目标；精确名与 Alias 查询稳定。

## 里程碑

### M0 · 约定与评测基线

- Git 仓库、忽略规则、领域语言、施工规范、架构决策落盘。
- 建立基准查询集格式与初始人工种子集。
- 验收：基准文件可被评测命令读取，预期 Tag 均存在于快照。

### M1 · 离线词典

- 只读导入 Tag、Wiki、Alias 到派生 SQLite。
- 建立 FTS5 与规范化查询索引。
- CLI 支持 `import`、`search`、`get`、`info`、`eval`。
- 验收：
  - 导入行数与源库对账。
  - 精确名、空格/大小写容错、Alias 歧义、FTS 查询可用。
  - 小型 fixture 行为测试通过。
  - 真实库导入与查询冒烟通过。

### M2 · 自然语言查询 MVP

- General/Meta 优先接入 Qwen3 Embedding 4B。
- FTS + 向量多路召回，RRF 或等价融合，Tag 级聚合。
- 中文查询改写仅在评测证明收益后启用。
- 多概念查询按子概念分组返回。
- 简洁 Web UI。
- 验收：固定 Benchmark 优于 M1 FTS 基线，精确与 Alias 无回归；记录延迟与资源。

### M3 · Research Agent 试点

- 整合 `E:/code/search_agent` 内核：LLM 客户端、SearXNG、抓取、act 循环、JSONL 断点续跑。
- 淘汰旧任务、跨项目路径、明文密钥与本机代理硬编码。
- 研究队列默认资格 `post_count >= 100`，队列内按图量降序。
- 建立 Evidence Store：原文、URL、引用、模型版本、研究时间。
- 预算耗尽输出 `unknown/failed`，不强制结论；引用必须可定位。
- 先研究 50–100 个分层抽样 Tag，人工抽查。
- 验收：
  - 无伪引用、无覆盖官方知识。
  - Research Candidate 默认不进入检索。
  - 发布前后 Benchmark 对照显示目标查询改善且无整体回归。

### M4 · 关系与同步

- 按查询需求补充共现、易混淆概念、代表性帖子。
- 本站恢复后导入官方 Implication 与增量更新。
- 图检索只由官方关系和已发布证据关系构成。
- 验收：关系确实改善检索、辨析或扩展，而不是仅生成展示图谱。

### M5 · Agent 服务化

- 稳定 REST 与 MCP 薄封装，复用同一个查询模块。
- 输出结构化状态：`ok`、`no_match`、`ambiguous`、`low_confidence`。
- 支持批量查询与紧凑输出格式。
- 验收：人类接口稳定后，Agent 接口不引入第二套业务逻辑。

## 明确推迟

- Qdrant、LightRAG、GraphRAG、LlamaIndex、Haystack 等框架。
- Sparse Vector、Cross-Encoder Reranker。
- 跨图站统一实体框架。
- Global Search、社区摘要。
- 全量 Tag 研究。
