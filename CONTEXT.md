# Danbooru Tag Knowledge

以 Danbooru Tag 实体为中心的知识与检索上下文。项目把官方快照、研究证据和语义表达整理成机器可查询的知识，供人类和下游标注 Agent 使用。

## Language

**Tag**:
Danbooru 中一个受管的视觉标签实体，拥有站内数字 ID、名称、类型和使用量。
_Avoid_: 关键词、label、词条

**Canonical Tag**:
当前快照中 Danbooru 认定的规范 Tag 名称；查询结果必须最终落到 Canonical Tag。
_Avoid_: 标准 tag、真实 tag

**Alias**:
指向某个 Canonical Tag 的替代名称。一个 Alias 可能指向多个 Canonical Tag，此时它是歧义 Alias，必须返回全部候选。
_Avoid_: 同义词、旧名

**Tag Type**:
Danbooru 官方标签类别：general、character、copyright、artist、meta。
_Avoid_: 分类、category（数据库中另有一套非官方分类，不使用）

**Post Count**:
快照中 Tag 的帖子使用量，是研究优先级和研究资格的默认判据。
_Avoid_: 热度、频率

**Source Snapshot**:
某个时间点导入的 Danbooru 数据库原文。Snapshot 只读、可追溯，不会被本项目修改。
_Avoid_: 原始库、线上数据

**Derived Store**:
由 Source Snapshot 确定性生成的查询库，包含规范 Tag、Alias、Wiki 和索引；可以随时删除重建。
_Avoid_: 缓存、业务库

**Semantic Document**:
用于把自然语言查询映射到 Canonical Tag 的文本，一条 Tag 可以有多条。它服务于检索，不承担举证责任。
_Avoid_: Wiki、释义、证据

**Evidence**:
支持某条知识或研究结论的原始材料及可定位引用。Evidence 用于研究、审核和解释，不直接参与普通 Tag 排序。
_Avoid_: 语义文档、检索片段

**Semantic Retrieval**:
从自然语言或名称输入找到 Canonical Tag 的只读检索任务。
_Avoid_: 问答、RAG

**Evidence Retrieval**:
为某个 Tag 查找、解释和审核证据的只读检索任务。
_Avoid_: 标签搜索

**Research Candidate**:
Research Agent 生成、尚未通过证据校验和审核的知识。Candidate 默认不进入 Semantic Retrieval 索引。
_Avoid_: 已发布知识、临时数据

**Published Knowledge**:
带来源、快照版本和审核状态，并允许进入普通检索索引的知识。
_Avoid_: 模型输出、生成结果
