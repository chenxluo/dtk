# M2 语义索引用 NumPy NPZ 暴力余弦，不引入向量数据库

M2 只索引图量 ≥100 的 General/Meta Tag（约 2.2 万个），Qwen3 Embedding 4B 返回 2560 维且忽略降维参数。以 float16 NPZ 保存归一化矩阵、分块 float32 点积，索引约 110MB、查询为一次稠密扫描，复杂度和依赖都低于引入 Qdrant 或 sqlite-vec。当索引范围扩大到数十万 Tag、查询延迟不可接受或需要增量更新时，再替换为专用向量索引；`VectorIndex.find` 是替换 seam。
