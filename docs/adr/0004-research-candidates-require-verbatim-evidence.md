# Research Candidate 只保留可逐字验证的证据

研究模型可能把“抓取失败但根据命名推断”包装成证据；旧原型成果中已经出现过这种伪引用。DTK 的 Research Candidate 因此以 JSONL 暂存，不直接进入 Derived Store 或向量索引。`status=completed` 必须附带本次会话实际抓取到的网页 URL，且 `quote` 必须是页面正文的连续子串；证据不足时只能输出 `status=unknown` 和未解决问题。发布流程未来可以替换存储位置，但“候选不自动发布、引用必须可定位”这两条不变。
