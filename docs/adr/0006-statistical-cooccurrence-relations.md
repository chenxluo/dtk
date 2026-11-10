# 共现关系是统计证据，不是官方 Tag 关系

Source Snapshot 含 4.33 亿条 `post_tags`，但当前快照没有官方 Implication 表。M4 先用本地帖子统计支持关系发现：`cooccurring_tags` 以共现次数做最低过滤，并用 Lift 排序，避免高频通用 Tag 仅凭热度主导。首次单 Tag 计算可能很重（真实 `black_footwear` 冷查询曾约 114 秒），结果缓存键包含源库路径、大小和 mtime，换快照不复用旧缓存。输出标记 `statistical_cooccurrence`，不得把它当作官方 Implication 或模型推断关系。
