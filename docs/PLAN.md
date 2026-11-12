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
| 官方 Implication | 本站恢复，实时 API 可用（经系统代理） | 已导入 47,069 条 active implication |

## 当前状态（2026-11-12）

- M0、M1、M2 完成：Git 基线、规范、Derived Store、混合检索、CLI、多概念分组与本地 Web UI 已建立。
- 真实快照导入对账：Tag 1,082,991 / Wiki 335,769 / Alias 616,836；导入耗时约 8 秒，派生库约 320 MB。
- 语义索引：21,773 个图量 ≥100 的 General/Meta Tag，Qwen3 Embedding 4B，2560 维，float16 NPZ 约 112 MB，构建约 388 秒。
- M1 FTS 基线：hit@1 0.565、hit@5 0.609、hit@10 0.652、MRR 0.589。
- M2 混合检索：hit@1 0.717、hit@5 0.848、hit@10 0.870、MRR 0.763；精确名与 Alias 优先级不变。
- 单条语义查询实测：Embedding 约 38–61ms，本机向量检索约 273–337ms。
- Web UI 已验证中文语义、详情展开、多概念分组（如「角色直视观众，非常长的头发」分别命中 `looking_at_viewer` 与 `long_hair`）。
- M3 研究内核完成 50 个高用量 General Tag 试点：completed 36、unknown 14、结构化失败 0；机械审核 invalid=0，累计 142,067 输入 / 141,285 输出 token。
- 人工显式发布 9 个研究候选到 additive research 表（25 条语义文档、11 条证据）；`light_brown_hair`、`striped`、`white_legwear` 继续不发布。
- 已发布研究通过 `embed-research` 生成独立 25 文档向量索引；仅余弦 ≥0.65 的研究文档进入召回。目标查询 `单眼眨眼并且张嘴笑 -> ;d` 由 Top 10 未命中改善为排名第 1，47 条 Benchmark hit@5 0.830→0.851、MRR 0.746→0.756，未见整体回归。
- Research 内核已修复 429 重试、SSRF 私网防护、LLM 缓存键污染、零进度循环、失败成本统计与并发输出锁。
- 新增可选 `--intent` 本地 LLM 意图门（只判断视觉查询，不生成 Tag）。50 条 Benchmark 含 3 个无意义/非视觉负例：no_match_accuracy 由 0→1，正例检索指标不变；Web UI 实测返回空结果，无浏览器错误。
- M4 已增加按需共现关系：CLI `related` 使用本地 post_tags、共现阈值和 Lift 排序，并提供包含快照身份的磁盘缓存；真实 `black_footwear` 查询验证通过，冷计算仍需离线预算。官方 Implication 已于本站恢复后导入（见上）。
- M5 已提供本地 REST 与 stdio MCP 薄封装，Agent 工具为 `search_tags`/`get_tags`；真实 MCP 进程与语义搜索验证通过。研究写入接口不对外暴露，接入方式见 `docs/AGENT.md`。
- 本站恢复后已导入官方 Tag Implication：47,069 条 active 关系入库为加性表 `tag_implications`（48 页、约 109 秒）；`get_tag`/MCP/REST 自动附带 `implications`（如 `pantyshot -> panties`），见 ADR 0007。
- 新增 `check-upstream`：本站最新帖 12,527,336（2026-11-12）对比快照 12,056,584（2026-08-23），快照落后约 47.1 万帖，只报告不自动同步。
- 研究模型可切换到局域网 `qwen3.8-flash-next-unsloth-ud-iq4_xs-v31`（262,144 上下文，自签 HTTPS 经 `--insecure-tls` + `--key-file key/qwen3.8flash.txt` 接入）。
- 新增免 key 的 Parallel MCP 搜索 provider（`--search-provider parallel-mcp`，走 `https://mcp.parallel.ai/mcp` 的 `web_search`，免费低限额）：实测首条命中即 Danbooru 官方 Wiki/论坛页，显著优于 SearXNG/Bing；不使用付费层级。配套修复 DNS 污染误杀（代理下域名校验放行）与 Chrome UA 反被 403 的问题后，3 个两轮 unknown 的标签一次烟测全部 completed，证据全部来自官方 Wiki。
- 建库管线开工(ADR 0008):官方 Wiki 补缺同步(`import-wikis`,7,750 个候选:非 artist ≥100 帖 + artist ≥1k 帖)、ffdkj 中文译名导入(336k,MIT)、`rebuild-index` 合并 FTS、`enrich` LLM 增强层(联想说法/facets/中文定义)均已实现;检索文档与知识文档分离,`tag_details` 视图统一合并视图,wiki 合并优先级 upstream > 快照。
- 研究提示词已加入二次元主题约束：ACG 语境优先、多义词取二次元义项、优先萌娘百科/pixiv 百科等来源（SearXNG 与 Parallel 共用）。

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

- General/Meta、`post_count >= 100` 的 Tag 接入 Qwen3 Embedding 4B（实测固定 2560 维）。
- 语义索引为归一化 float16 NPZ + 分块 float32 余弦；FTS 与向量用 RRF 融合，并加入图量对数先验，Exact/Alias 优先级不变。
- 多概念查询按明确分隔符拆分子概念并分组返回，不做无标记的语言猜测。
- 简洁 Web UI：语义查询、命中原因、复制 Tag、完整 Wiki、别名与多概念分组。

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
