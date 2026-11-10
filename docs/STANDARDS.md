# 施工规范

## 数据安全

1. Source Snapshot 永远只读。项目不得写入、迁移或修改 `core_data_new_v3.db`。
2. Derived Store、向量索引、缓存、日志一律放在 `data/`、`out/`、`cache/`、`logs/`，不入 Git。
3. 密钥只放 `.env` 或环境变量， `.env` 不入库；从外部项目复制代码前先检查密钥。
4. 导入必须生成快照清单：源路径、大小、mtime、最新帖子时间、行数、导入时间、Schema 版本。

## 知识可信度

1. 普通查询返回的每个 Tag 必须来自 Derived Store，模型不得凭记忆发明 Tag。
2. 官方快照数据优先级最高；研究知识不得改写官方 Wiki、Alias 或关系。
3. Evidence 必须能定位到原文；抓取失败、推断、记忆都不算 Evidence。
4. Research Candidate 默认不进入检索索引；只有发布状态的知识可以参与普通查询。
5. `match_score` 只表示查询匹配度；知识可靠性使用 `provenance + evidence + review_status` 表达，不使用模型自报 confidence。

## 研究队列

1. 默认研究资格：`post_count >= 100`；配置项可调。
2. 队列内按 `post_count` 降序；`post_count = 0` 直接忽略。
3. 图量低于门槛的 Tag 仅在用户显式指定时研究，检索失败不自动降低门槛。
4. 研究分层：缺 Wiki、缺中文语义、检索失败线索分别排队，不混合排序。

## 查询职责

1. Retrieval 只读，不得触发研究、写库或重建索引。
2. Semantic Retrieval 负责找到 Canonical Tag；Evidence Retrieval 负责证明与解释，二者不共用排序目标。
3. 歧义 Alias 必须返回全部候选，不得静默选择第一个。
4. 查询必须能显式返回 `no_match`、`ambiguous`、`low_confidence`，不强行编造答案。

## 模型策略

1. 默认使用本地 LM Studio：生成/改写用 `qwen3.8-27b`，Embedding 首选 `text-embedding-qwen3-embedding-4b`。
2. DeepSeek 只作为显式配置的对照方案，不做透明自动 fallback。
3. 更换或引入模型必须有同一 Benchmark 上的对照结果和成本记录。

## 代码与测试

1. Python 3.10+，优先标准库；新依赖必须说明不可用标准库替代的原因。
2. 公共入口是 Python API 与 CLI；Web、REST、MCP 只做薄封装，不复制业务逻辑。
3. 行为测试走公共接口，不测私有函数和内部表结构。
4. 每个里程碑至少有一个真实库冒烟命令和一个可重复 Benchmark 结果。
5. 基准查询集只收录人工确认的 query→Tag 映射，不得用模型生成后直接入库。

## 文档

1. 里程碑完成或方向变化时更新 `docs/PLAN.md`。
2. 领域词冲突先改 `CONTEXT.md`，再改代码命名。
3. 难以逆转的架构选择记录到 `docs/adr/`。
