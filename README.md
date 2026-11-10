# Danbooru Tag Knowledge (DTK)

以 Danbooru Tag 实体为中心的离线知识库与检索服务。M1 提供只读导入、精确名、Alias 与 FTS 查询、CLI 和检索评测。

## 快速开始

Python 3.10+，M1 无第三方运行时依赖。

```powershell
# 1. 从只读 Source Snapshot 构建 Derived Store
$env:DTK_SOURCE_DB = "G:/标注任务/metadata/core_data_new_v3.db"
python -m dtk import

# 2. 查询
python -m dtk search "Looking At Viewer"
python -m dtk search "角色直视观众" --limit 5
python -m dtk get looking_at_viewer
python -m dtk info

# 3. 检索评测
python -m dtk eval eval/benchmark.jsonl --k 1,5,10
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
