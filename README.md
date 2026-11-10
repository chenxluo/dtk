# Danbooru Tag Knowledge (DTK)

以 Danbooru Tag 实体为中心的离线知识库与检索服务。当前提供只读导入、精确名、Alias、FTS 与 Qwen3 向量混合检索、多概念分组、CLI、本地 Web UI 和检索评测。

## 快速开始

Python 3.10+；语义检索需要 NumPy 与本地 LM Studio Embedding 服务。

```powershell
# 1. 从只读 Source Snapshot 构建 Derived Store
$env:DTK_SOURCE_DB = "G:/标注任务/metadata/core_data_new_v3.db"
python -m dtk import

# 2. 为图量 >=100 的 General/Meta Tag 构建语义向量索引
python -m dtk embed

# 3. 查询；存在向量索引时自动使用 FTS + Vector RRF 混合检索
python -m dtk search "Looking At Viewer"
python -m dtk search "角色直视观众，非常长的头发" --limit 5
python -m dtk search "角色直视观众" --no-vectors
python -m dtk get looking_at_viewer
python -m dtk info

# 4. 启动本地人类查询界面
python -m dtk serve --port 8765

# 5. 检索评测；--no-vectors 可复跑 M1 FTS 基线，--intent 可评估无意义查询拦截
python -m dtk eval eval/benchmark.jsonl --k 1,5,10
python -m dtk eval eval/benchmark.jsonl --k 1,5,10 --no-vectors
python -m dtk eval eval/benchmark.jsonl --k 1,5,10 --intent

# 6. 研究缺 Wiki 的高用量 General Tag（输出 Candidate，不自动入索引）
python -m dtk research --types general --limit 10 --dry-run
python -m dtk research --types general --limit 10
# 显式指定 Canonical Tag 可绕过图量门槛，例如用于人工例外
python -m dtk research --tag looking_at_viewer --dry-run

# 7. 人工审核 out/research 后，显式发布接受的研究候选（不改官方 Wiki）
python -m dtk publish-research out/research/pilot_general.jsonl --tags black_footwear,white_footwear

# 8. 发布内容变化后重建独立 research 向量索引
python -m dtk embed-research

# 9. 机械复核研究 JSONL；非零 invalid 表示需要人工处理
python -m dtk audit-research out/research/pilot_general.jsonl
```

Derived Store 默认写入 `data/dtk.db`，可用 `--db` 或 `DTK_DB` 覆盖。`data/` 不入 Git，Derived Store 可随时删除重建。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 数据安全

- Source Snapshot 只读，本项目不写入 58GB 源库。
- Derived Store 记录源路径、大小、mtime、最新帖子时间与行数对账。
- 密钥与本地路径放 `.env` 或环境变量，`.env` 已忽略。

## 文档

- 施工计划：`docs/PLAN.md`
- 施工规范：`docs/STANDARDS.md`
- 领域语言：`CONTEXT.md`
- 架构决策：`docs/adr/`
- 研究试点记录：`docs/RESEARCH-LOG.md`
