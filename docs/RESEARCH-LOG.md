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

## 2026-11-11 · 扩到 50 个高用量缺 Wiki General Tag

命令使用 `--limit 50`；断点跳过已有 12 个，新研究 38 个。

| 指标 | 本轮新增 | 累计 |
|---|---:|---:|
| 研究单元 | 38 | 50 |
| completed | 26 | 36 |
| unknown | 12 | 14 |
| 结构化失败 | 0 | 0 |
| 输入 token | 114,609 | 142,067 |
| 输出 token | 106,599 | 141,285 |
| 耗时 | 1,840.7 秒 | 约 39.6 分钟 |

机械审核结果：`python -m dtk audit-research` 为 records=50、invalid=0。所有 completed 的 quote 都能重新定位到记录中的抓取正文；这不等于人工语义审核通过。

unknown 共 14 个：

`white_legwear`、`striped`、`cross-laced_footwear`、`french_braid`、`multicolored_background`、`remodel_(kantai_collection)`、`;o`、`oni_horns`、`wall`、`leaning_on_person`、`disembodied_linked_eye`、`missing_headwear`、`blue_neckwear`、`pink_headwear`。

其中 `remodel_(kantai_collection)` 与 `oni_horns` 用尽 6 个动作后仍未知；预算耗尽没有生成伪定义。`pink_headwear` 未获取有效页面，直接 unknown。

本轮没有新增发布。新增 completed 中有多条只得到 e621 别名信息（例如 `light_blue_hair`、`dark_blue_hair`、`qipao`、`beige_background`），在本地 Danbooru 快照缺少对应官方关系时，不把跨站别名迁移进知识库。这些 Tag 仍保留原始候选，等本站恢复或人工审核后处理。

## 2026-11-12 · 本站恢复、Parallel 预留与二次元主题约束

- Danbooru 本站恢复（本机经系统代理访问）。官方 Tag Implication 已由 `import-implications` 导入：47,069 条 active，48 页约 109 秒，幂等加性表；这是官方数据，不属研究产出（ADR 0007）。
- `check-upstream` 首次量化快照滞后：本站最新帖 12,527,336，快照 12,056,584，落后约 47.1 万帖。多条 27B 试点 completed 依据的 e621 别名关系，现在可回到本站官方数据核对后择机发布。
- 新增 Parallel Search API provider（`--search-provider parallel`）。经文档核实：`/v1/search` 需 `search_queries` 短关键词 + `objective`，`x-api-key` 认证，默认限额 600 请求/分钟，turbo/fast 约 $1/1000 次，新账号有注册赠金。key 未配置，客户端已单测覆盖，真实检索质量待拿到 key 后与 SearXNG 对比。
- 研究提示词加入二次元主题约束：Danbooru 定位为 ACG 图站、多义词取二次元义项、优先萌娘百科/pixiv 百科/Danbooru Wiki/e621 Wiki/动漫 Fandom；Parallel 的 objective 模板同样内嵌该主题。目的：压住 `wall`、`striped` 这类通用词跑偏，以及多义项词条的词典义污染。
- 研究模型接入局域网 `qwen3.8-flash-next-unsloth-ud-iq4_xs-v31`（262,144 上下文）：`--key-file key/qwen3.8flash.txt --insecure-tls`。与 27B 的对比见下一节。

## 2026-11-12 · flash vs 27B 对比

意图门控（50 条 Benchmark，`--intent --intent-key-file key/qwen3.8flash.txt --insecure-tls`）：

| 指标 | 27B | flash |
|---|---:|---:|
| no_match_accuracy（3 负例） | 1.0 | 1.0 |
| 正例 mrr / hit@1 / hit@5 / hit@10 | 0.756 / 0.702 / 0.851 / 0.872 | 完全相同 |

研究复测（27B 试点中 6 个全部 unknown 的标签，`--tag` 显式指定，同预算 6 acts）：

| 指标 | 27B（当时） | flash |
|---|---:|---:|
| completed | 0/6 | 2/6（`wall`、`pink_headwear`） |
| unknown | 6 | 4 |
| 结构化失败 | 0 | 0 |
| 输入/输出 token | — | 52,268 / 1,343 |
| 耗时 | — | 294.1 秒（约 49 秒/Tag） |

- 机械审计 invalid=0，全部 quote 可在抓取正文中定位。
- `wall` 的 flash 定义走的是 e621 `wall_(structure)` 别名链，明确区分 fence/window，正是二次元主题约束想压住的多义项场景。
- 本站恢复后核对官方数据：live Danbooru 已有 `wall` 官方 Wiki；`pink_headwear` 已被官方弃用（too broad）。两者都不发布——官方数据优先，研究不覆盖官方。
- 结论：flash 在意图门控上与 27B 持平，在研究上多解出 2 个 27B 放弃的标签，输出 token 约 27B 的 1/12，速度相当；后续研究默认用 flash（`--key-file key/qwen3.8flash.txt --insecure-tls`），27B 保留对照。
- 连带修复：模型现在会构造中文路径 URL（如萌娘百科 `/鬼角`），`http.client` 请求行 ascii 编码直接崩溃；Fetcher 已在请求前做百分号编码（含回归测试）。CLI 流统一重配 UTF-8，后台管道不再因中文进度行崩溃。

## 2026-11-12 · 免 key Parallel MCP 实测

核实官方文档与实测 `https://mcp.parallel.ai/mcp`（streamable HTTP，JSON-RPC）：不带任何凭证即可用 `web_search`/`web_fetch`/`web_decisions`，低限额免费；`web_research` 才需要登录。决定不使用付费层级，已实现 `ParallelMCPSearchClient`（`--search-provider parallel-mcp`，默认 0.5 QPS 节流 + 磁盘缓存 + 会话过期重初始化）。

质量对比（同一查询思路）：

| Provider | `oni_horns` 相关首条结果 |
|---|---|
| SearXNG/Bing（此前） | Taco Bell、彩票等完全无关页 |
| Parallel MCP（免 key） | `danbooru.donmai.us/wiki_pages/oni_horns` 官方 Wiki，后续为 oni/skin-covered_horns/horns 相关词条 |
| Parallel MCP（免 key） | `french_braid` 查询首条为 Danbooru 论坛『fixing the french_braid tag』与 french_braided_* Wiki 系 |

免费路径走 `fast` 模式、每结果自带 excerpt，研究 act 循环可直接据 excerpt 决定是否 fetch 全文。真实研究烟测见输出 `out/research/parallel_mcp_smoke.jsonl`。

研究烟测（`french_braid`、`blue_neckwear`、`leaning_on_person`，此前 27B 与 flash 两轮均 unknown）：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| completed | 0/3 | **3/3**（1–2 acts/Tag） |
| 结构化失败 | 0 | 0 |
| 耗时 | 94.7 秒 | 62.5 秒 |

三条 verdict 的证据全部来自 `danbooru.donmai.us` 官方 Wiki，机械审计 invalid=0。`french_braid`、`blue_neckwear` 的官方 Wiki 实为弃用通知（官方建议改用更具体标签），定义里已如实转述。候选在 `out/research/parallel_mcp_smoke2.jsonl`，等待人工决定是否发布。

烟测同时暴露并修复两个抓取问题：

1. GFW 对 Danbooru 的 DNS 污染会返回假 AAAA（`2001::/32` 保留段），SSRF 防护的“任一解析地址在封锁段即拒绝”误杀全部 Danbooru 抓取。现在走系统代理时域名校验放行（解析实际发生在代理侧，且字面私网 IP 仍被拒绝）。
2. Fetcher 伪装完整 Chrome UA 反而被 Cloudflare 403；改回诚实的 `DTK-research/0.3` UA 后官方 Wiki 页直接 200。

## 2026-11-12 · 建库管线:现状盘点与缺口分层

盘点(按 Derived Store 真实计数):

| 类别 | ≥100 帖 | 快照 wiki | 缺口 | 抽查官方线站 |
|---|---:|---:|---:|---|
| general | 21,426 | 20,516 | 910 | 16/16 有 |
| meta | 347 | 3 | 344 | 8/8 有 |
| copyright | 4,887 | 9 | 4,878 | 8/8 有 |
| character | 18,421 | 17,291 | 1,130 | — |
| artist | 20,466 | 3 | 20,463 | — |

结论:缺口主体是"快照滞后"而非"真缺知识"——研究管线只应兜底官方也没有的部分。据此开工三层管线(ADR 0008):P1 官方 Wiki 补缺同步(含 other_names 中/日别名)、P2 ffdkj 336k 中文译名导入(MIT,含全部 ≥10 帖标签)、P2b FTS/向量/get_tag 合并重建、P3 flash 增强层(联想说法/facets/中文定义,≥10k 帖先行)。检索基准基线(增强前,同索引):hit@1 0.702 / hit@5 0.851 / hit@10 0.872 / MRR 0.756。

运维教训:24 并发会把本机代理打挂(吞吐降到 0),8 并发稳定 1.2/s;Danbooru 对诚实 UA(`DTK-*`)直连可用。

## 2026-11-12 · 建库管线执行结果

| 步骤 | 结果 |
|---|---|
| P1 官方 Wiki 同步 | 7,750 个候选全部检查:7,378 命中(95.2%),372 个官方确认无 Wiki(真正的研究目标池) |
| P2 ffdkj 中文译名 | 329,760/336,155 匹配(98.1%);60 条抽查全对 |
| P3 flash 增强层(≥10k 帖) | 2,584/2,584 成功,0 失败(纠错重试兜住坏 JSON);603k in / 325k out tokens,约 2.5 小时 |

增强层质量抽样(`search_terms` 为中文口语联想词):

| 标签 | 中文定义(截选) | 联想词样例 |
|---|---|---|
| thighhighs | 长度覆盖至大腿的长筒袜… | 绝对领域、白丝、黑丝、勒肉感 |
| animal_ear_fluff | 兽耳内侧突出的柔软绒毛… | 兽耳内毛、狐耳白毛、毛茸茸的耳朵 |
| wedding_dress | 婚礼仪式中穿着的白色礼服… | 新娘礼服、带面纱的裙子 |
| black_legwear | 黑色腿部服饰统称,已废弃… | 黑色丝袜、黑色打底裤、black pantyhose |

运维记录(429 封禁与代理塌陷教训):并发持续打满会让本机代理假死、并触发 Danbooru 429;稳定参数为分块(500/块)× 4 并发 × 1.5s 节流 + 失败指数退避 5/10/20/40 分钟;flash(局域网)不受影响,8 并发稳定。

## 2026-11-13 · 建库管线验收:基准对比

50 条基准(带意图门控,同一评测命令):

| 指标 | 增强前 | 增强后 | Δ |
|---|---:|---:|---:|
| MRR | 0.756 | **0.872** | +0.116 |
| hit@1 | 0.702 | **0.809** | +0.107 |
| hit@5 | 0.851 | **0.915** | +0.064 |
| hit@10 | 0.872 | **0.979** | +0.107 |
| no_match 准确率 | 1.000 | **1.000** | 持平 |

47 条正例仅剩 1 条失败(`en-from-below`:英文描述"camera looking up at the character" 未中 `from_below`,向量被 looking_* 家族分走)。中文查询(如"黑色鞋子"→`black_shoes`、"过膝袜"→`thighhighs`)现在直接命中 cn_name/联想词。知识层:`get_tag` 对 7,378 个原缺口标签返回官方 Wiki + other_names,中文译名覆盖 329,760 个标签。

## 2026-11-13 · 夜间管线 + P4 研究:真缺 Wiki 的长尾标签

夜间 enrich ≥1k 帖全量完成(6,085/6,085,0 失败;基准复测 MRR 0.881 / hit@1 0.830 / hit@5 0.936 / hit@10 0.979,no_match 1.0)。期间发现研究队列仍按快照列选目标、混入已官方同步的标签——已修复(`52e25ef`):队列排除 `upstream_wikis` 有正文的标签;另加 CLI 分发守卫测试(前一晚 `run_research` import 被覆盖导致夜链首步崩溃,`a354812` 修复)。

修复后真目标 42 个(general/meta ≥100 帖,快照与官方线站均无 Wiki):**10 completed / 32 unknown / 0 failed,审计 invalid=0**(88 分钟,835k in tokens)。unknown 占 76% 属实——这批长尾(`moe2019`、`fake_ad`、各校服标签等)连萌娘/e621 都无权威条目,unknown 即正确答案。

10 个发布候选(证据逐字校验通过):`symbol-only_commentary`、`game_cg`、`=_hair_ornament`、`sweaty_feet`、`ursus_empire_logo`、`arms_around_self`、`pink_frills`、`metadata_pun`、`qingming_sword`、`domain_expansion`。候选在 `out/research/buildout_true_missing.jsonl`,等待人工 `publish-research`。首轮混入冗余目标的运行保留在 `out/research/buildout_missing.jsonl`(49 条,其中官方已有 Wiki 的按"官方优先"不发布)。
