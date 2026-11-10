# Research Agent 试点记录

## 2026-11-11 · missing-Wiki General 试点

命令：

```powershell
python -m dtk research --types general --limit 3 --max-acts 6 `
  --out out/research/pilot_general.jsonl --cache cache/research --json
```

### 运行结果

| 指标 | 数值 |
|---|---:|
| 研究单元 | 3 |
| 结构化成功 | 3 |
| unknown | 0 |
| 失败 | 0 |
| 输入 token | 8,229 |
| 输出 token | 9,387 |
| 耗时 | 126.5 秒 |
| 逐字 quote 校验 | 3/3 通过 |

模型：`qwen3.8-27b`。所有结论都来自直接抓取的 e621 同名 Wiki；本轮 `queries_used` 为空，因为 Danbooru 本站不可用且 SearXNG 结果不可信，模型按 prompt 中的跨站 URL 直接抓取。

### 人工审核

| Tag | 机械校验 | 人工结论 |
|---|---|---|
| `black_footwear` | 通过 | 接受为 Research Candidate；定义与 e621 证据语义一致，但仍非 Danbooru 官方 |
| `white_footwear` | 通过 | 接受为 Research Candidate；定义与 e621 证据语义一致，但仍非 Danbooru 官方 |
| `light_brown_hair` | 通过 | 暂缓发布；e621 显示该站别名到 `brown_hair`，但本地 Danbooru 快照没有这条官方 Alias，且候选语义文档写了「棕色头发 / brown hair」，可能引入跨站体系差异 |

三条记录都保持 `evidence_verified=true` 的机械含义：URL 在本次会话抓取过，quote 是页面正文连续子串。该字段不等于人工语义审核通过，也不代表候选已发布。

### 发现与处理

1. 初次试点 3/3 因 `semantic_documents` 形状不符合 schema 失败；prompt 已补充逐字段 JSON 示例，复跑 3/3 成功。
2. 初次失败单元的 token 未计入 stats；已修复，失败也会进入成本统计。
3. 网页瞬时失败曾可能被缓存为 `None`；已改为失败与空页不入缓存。
4. LLM 429/408 现在与 5xx 一样重试。
5. 研究抓取器现在拒绝 loopback、私网、链路本地、保留地址和重定向到这些地址的目标，避免模型诱导 SSRF。
6. act 循环增加总轮次上限，避免小模型重复动作导致无上界消耗。
7. 研究输出增加跨进程文件锁；LLM/搜索/抓取缓存改为进程唯一临时文件原子写入。
8. LLM 缓存键包含 endpoint 与最终请求 payload，不再跨配置复用旧响应。

### 当前边界

- Research Candidate 初始只存在于 `out/research/*.jsonl`；只有人工显式接受的 completed 记录才可进入 additive research 表。
- 已发布研究目前只改善 `get_tag` 与 Web UI 解释，不参与 FTS、向量排序或官方字段。
- SearXNG/Bing 当前返回与查询无关的结果，未通过检索质量验收；在修复前，研究 prompt 依赖明确 URL 提示。
- e621 证据只能证明跨站语义，不能当作 Danbooru 官方释义；发布前必须保守处理别名和分类体系差异。

## 2026-11-11 · 扩批到 12 个 General Tag

命令同上，`--limit 12`。断点续跑跳过前 3 个，新研究 9 个。

| 指标 | 扩批新增 | 累计记录 |
|---|---:|---:|
| 研究单元 | 9 | 12 |
| completed | 7 | 10 |
| unknown | 2 | 2 |
| 结构化失败 | 0 | 0 |
| 输入 token | 19,229 | 27,458 |
| 输出 token | 25,299 | 34,686 |
| 耗时 | 410.0 秒 | 约 536.5 秒 |

### 人工发布决策

已显式发布 9 个：

- `black_footwear`
- `white_footwear`
- `;d`
- `red_footwear`
- `black_legwear`
- `looking_away`
- `black_headwear`
- `blue_footwear`
- `white_headwear`

发布命令写入 additive research 表，共 25 条语义文档、11 条证据；不改官方 Wiki、Alias、Tag 字段或向量索引。

继续不发布：

- `light_brown_hair`：e621 alias 到 `brown_hair`，与本地快照 Alias 不一致，跨站别名不迁移。
- `white_legwear`：未抓到可引用正文，status=unknown。
- `striped`：e621 页面为空且仅显示跨站 alias 到 `stripes`，status=unknown。

### 发布验证

- `python -m dtk get black_footwear` 返回中文研究定义、语义文档与 e621 证据。
- `python -m dtk get ';d'` 返回完整歧义说明。
- `python -m dtk get light_brown_hair` 的 `research` 为 `null`，确认暂缓项未发布。
- Web UI 查询「黑色鞋子或靴子」后展开 `black_footwear`，能看到已发布研究定义、语义表达与证据 URL，浏览器控制台无错误。

## 2026-11-11 · Research 向量索引与 Benchmark

执行 `python -m dtk embed-research`，从 25 条已发布文档生成 `data/research_vectors.npz`，不重建官方索引。

先新增人工基准：`单眼眨眼并且张嘴笑 -> ;d`。接入 research 向量前，该查询 Top 10 不含 `;d`。

首次合并把小型 research 索引的 25 条文档全部纳入 RRF，导致 `black_legwear`、`blue_footwear` 等无关 Tag 污染其他查询。改为：

1. research 文档余弦相似度 < 0.65 直接丢弃；
2. 达到阈值的 research 命中排在官方召回之前参与 RRF；
3. 官方 Tag 聚合仍按 post_count 对数先验。

修复后同一查询 `;d` 排名第一，原先无关污染消失。47 条 Benchmark 对照：

| 指标 | 无 research 向量 | 有 research 向量 |
|---|---:|---:|
| hit@1 | 0.702 | 0.702 |
| hit@5 | 0.830 | 0.851 |
| hit@10 | 0.851 | 0.872 |
| MRR | 0.746 | 0.756 |

说明：这是针对 `;d` 的目标查询改善，不代表小样本上所有语义能力都得到充分验证；阈值需要在研究文档扩大后重新校准。
