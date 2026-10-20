# OHR 数据恢复与索引维护

完整发布包已包含正式正文、PDF和匹配的索引，首次启动无需下载数据或重建索引。
GitHub 源码仓库通过 `.gitignore` 排除整个 `data/public_benchmark/ohr/` 目录，包括正文、PDF、清单和派生索引。使用 OHR 知识库前，请从完整发布包恢复该目录，再核对正文、原件和索引状态。

## 核对原件

安装依赖后，从项目根目录执行：

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/verify_ohr_originals.py
```

本包不含研究Parquet，日常核对无需加 `--include-parquet`。
缺失或哈希变化时，优先从完整发布包恢复。上游固定版本为
`https://huggingface.co/datasets/opendatalab/OHR-Bench/tree/7f833e3eda9a571a9ea545a8f6d476fa1685033d`。
原件应位于 `data/public_benchmark/ohr/pdfs/<分类>/<文件>.pdf`，具体路径以 `large_files_manifest.json` 为准。

## 查看与重建索引

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --status
# 正文变化后显式构建关键词索引：
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py
# 配置有效模型密钥后，才可构建真实向量：
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --embeddings
```

构建会读取项目根目录 `.env`，请先确认其中的数据路径与模型配置。
修改正文或嵌入模型后，旧向量可能失效；应按健康状态显式重建，系统不会以不匹配向量替代真实检索。
完整OHR重建可能耗时并产生云调用费用。初始化脚本不是首次运行的必要步骤。
