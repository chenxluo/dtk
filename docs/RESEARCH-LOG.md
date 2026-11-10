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

- Research Candidate 只存在于 `out/research/*.jsonl`，未进入 Derived Store、FTS 或向量索引。
- SearXNG/Bing 当前返回与查询无关的结果，未通过检索质量验收；在修复前，研究 prompt 依赖明确 URL 提示。
- e621 证据只能证明跨站语义，不能当作 Danbooru 官方释义；发布前必须保守处理别名和分类体系差异。
