# Source Snapshot 不可变，Derived Store 可重建

58GB 源库是唯一完整 Danbooru 快照，且包含帖子业务数据；直接在其上建索引会把项目产物与外部资产耦合，损坏风险高。所有项目数据都由只读导入生成到独立 Derived Store，导入过程记录源路径、大小、时间、行数和最新帖子时间。Derived Store 可以随时删除重建；当 Danbooru 本站恢复后，新快照进入新的 Derived Store 版本，而不是原地覆盖历史知识。
