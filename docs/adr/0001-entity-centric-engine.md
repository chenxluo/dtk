# 自建实体中心引擎，起点为 SQLite + FTS5

项目需要混合检索、证据追踪和离线研究，但业务实体已经是明确的 Danbooru Tag，官方结构化数据也已存在。直接采用 Qdrant、LightRAG、GraphRAG、LlamaIndex 或 Haystack 作为基础框架，会让知识库所有权迁入通用 RAG 产品，并引入与 Tag 映射任务无关的复杂度。因此第一版自建小型实体中心引擎：SQLite 保存 Tag、Alias、Wiki 与知识状态，FTS5 承担关键词召回，向量与图检索在 Benchmark 证明必要时再以独立索引层加入。参考框架只借鉴思想，不成为运行时依赖。
