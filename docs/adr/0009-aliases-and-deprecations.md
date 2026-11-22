# ADR 0009: 官方别名与弃用标记导入,检索层弃用降权

## 状态

已接受（2026-11-22）

## 背景

研究加成（2026-11-22 检索层适配）会浮起"已废弃但研究文档含口语联想词"的
tag：`黑色丝袜` 命中 `black_legwear`（官方 `is_deprecated=true`，live 帖数
已归零）而非现行 tag。快照没有弃用状态，也没有 Danbooru tag_alias 数据；
弃用无法从"是否为某别名的 antecedent"推导（`black_legwear` 无别名指向，
弃用与改名是两个独立字段）。

## 决策

- `dtk/import-aliases`：分页拉取 `/tag_aliases.json`（status=active）写入
  加性表 `tag_aliases`，结构与 `tag_implications` 相同（antecedent /
  consequent / status，上游 id 主键，幂等）。
- `dtk/import-deprecations`：分页拉取 `/tags.json?search[is_deprecated]=true`
  写入加性表 `tag_deprecations(name PRIMARY KEY)`。
- `get_tag` 附带 `deprecated: bool` 与 `aliased_to: [consequent]`（active
  别名指向）；无表时为 false / []。
- 检索层：弃用候选的研究加成归零，并扣 `DEPRECATION_PENALTY = 2×
  POPULARITY_PRIOR`——弃用 tag 无法用于新帖，固定罚分使其沉到任何活 tag
  之后但不被抹除。
- 别名不做检索分值转移：无别名的弃用 tag（black_legwear 类）无目标可转；
  有别名的转移可能把语义更差的目标顶上去。

## 理由

- 弃用标记与 implication 同源同权（官方数据），走相同的加性表路径，
  无需 evidence/审核。
- 罚分取 2× 先验上限：任何活 tag（先验 + RRF ≥ 0.016）都压过受罚弃用 tag，
  不需要逐查询调参。

## 后果

- 重建 Derived Store 后需重跑 `import-aliases` / `import-deprecations`。
- 8 条已发布研究的 tag 官方已弃用（black_legwear、looking_away 等
  "色彩+服饰统称"类）：知识层继续经 `get`（带 deprecated: true）服务，
  检索层自动降权，不撤发布。
- 基准 MRR 0.8837 → 0.9156（弃用 tag 让位），hit@5 / hit@10 / no_match
  不变；`赛博朋克边缘行者动画`→`cyberpunk_edgerunners` #1 保持。
