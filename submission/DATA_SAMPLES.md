# 文档样本与来源

可直接上传的11份独立合成样本位于 `data/rule_demo/documents/`，包含MD、TXT、PDF和DOCX。
样本均明确标注合成用途；`data/rule_demo/manifest.json` 保存文件哈希与样本说明。
`data/documents/` 是合成包生成脚本使用的两份种子文本。

真实公开资料位于 `data/public_benchmark/ohr/`，包含1261份官方校对正文及对应PDF。
正文、原件映射、来源哈希与真实检索产物成套交付。Chinook数据库及schema位于对应chinook目录。

这些样本用于展示解析、检索与证据核对机制。样本数量、工程检查或合成测试通过率均不能作为比赛准确率、人工忠实度或真实业务泛化能力。
