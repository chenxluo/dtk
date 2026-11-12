# ADR 0008: 检索增强分层——检索文档与知识文档分离

日期:2026-11-12
状态:已接受

## 背景

M2 检索把 Wiki 同时当作"知识文档"(给人/agent 看)和"检索文档"(喂 FTS/向量),带来三个命中率瓶颈:

1. 快照内 copyright/meta/artist 的 Wiki 几乎整体缺失(抽查显示官方线站对缺口标签 32/32 有 Wiki)。
2. 中文自然语言查询依赖跨语言 embedding,词表对齐缺口大(general ≥100 中文 Wiki 仅 291 条)。
3. Wiki 纯英文长文,信噪比低;无 Wiki 的标签向量文档只剩名称+别名。

## 决策

把两层分开,数据全部走附加表(模式与 ADR 0007 的 implications 一致),快照列永不改写:

1. **知识层**(get_tag/MCP/REST 返回):`upstream_wikis`(官方 Wiki 补缺,body NULL=官方确认无 Wiki)、`tag_translations`(ffdkj 中文名,MIT)、`research_publications`(证据式研究)。合并优先级:upstream Wiki > 快照 Wiki;中文定义:研究发布 > ffdkj 译名。
2. **检索层**(喂索引):`tag_details` 视图合并上述来源;FTS 五列(name/cn_name/aliases/other_names/wiki)bm25 加权 4/4/2/2/0.2;向量文档 = 名称+别名+cn_name+other_names+Wiki 前 2000 字符。`rebuild-index` 重建 FTS,`embed` 重建向量。
3. **增强层**(P3,未实施):flash 对 ≥10k 帖标签生成联想说法(zh/en)+facet 关键词,进 FTS/向量,以 50 条基准 hit@5/MRR 前后对比验收。

## 后果

- 官方数据始终优先;研究只兜底"官方也无 Wiki"的标签。
- 重建 Derived Store 后需重跑 import-implications / import-wikis / import-translations / rebuild-index(均为幂等增量导入)。
- bm25 权重与 P3 层级由基准驱动调整,不拍脑袋。
