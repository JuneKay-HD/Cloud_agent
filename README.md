# 小云助手 Cloud_Agent

面向赛题八的多模态问数、知识库问答与证据展示项目。后端采用 FastAPI，前端采用 Vue 3 + Vite，数据库使用 SQLite。

> **GitHub 源码版**：仓库包含应用源码、Chinook 数据库和规则问数示例。OHR 数据集目录 `data/public_benchmark/ohr/` 已排除，使用 OHR 知识库前需要从完整发布包恢复该目录，再按[数据恢复与索引维护](docs/OHR_DATA_RESTORE.md)核对状态。下文中的随包索引、原件和约 6 GiB 体积说明针对完整竞赛发布包。

项目包含应用源码、数据库、知识库原件和随包索引，可整体复制到其他目录运行。API密钥由使用者在本机配置。

## 运行环境

- Windows 10/11，PowerShell 5.1。
- **Python 3.12**。安装后执行 `py -3.12 --version` 检查；也可在启动时用 `-PythonPath` 指定Python 3.12完整路径。
- **Node.js 24 + npm**。本次验证使用Python 3.12.7、Node.js 24.12.0、npm 11.6.2。
- 首次安装需要连接Python与npm包源。后端版本见 [requirements.txt](backend/requirements.txt)，前端由 [package-lock.json](frontend/package-lock.json) 锁定。
- 部分扫描PDF可由本地RapidOCR识别；图片、幻灯片、扫描PDF的云视觉回退，以及完整云模型问答，需要自己的模型服务密钥。PPT/PPTX逐页解析还需要Windows Microsoft PowerPoint；普通问数和PDF原件预览不依赖PowerPoint。

完整目录含约6 GiB数据和检索、原页定位资料，安装依赖需要额外空间。请完整解压，建议放在路径较短的位置。整个目录可移动，数据路径相对本项目根目录解析。

## 快速启动

打开PowerShell，进入本README所在目录：

```powershell
py -3.12 --version
node --version
npm.cmd --version
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

脚本会创建 `backend/.venv/`，安装前后端依赖，在缺少 `.env` 时从 `.env.example` 创建配置，然后在后台启动服务并检查就绪状态。

- 网页：`http://127.0.0.1:5174`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- API文档：`http://127.0.0.1:8000/docs`

首次安装可能需要数分钟，日志保存在 `tmp/runtime/`。启动成功后可关闭启动终端。首次知识库健康检查需要读取正式正文和索引，耗时高于空项目。

首次启动无需初始化数据库。知识库正文与随包索引匹配时可直接使用；若知识库提示不可用、索引过期或文档数量异常，按[知识库检查与构建](#知识库检查与构建)处理。`/api/health` 返回正常仅表示后端可访问，知识库是否可用需要单独检查。

停止服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
```

停止脚本处理本目录启动脚本记录的进程；手动在其他终端启动的服务需在对应终端结束。

## 配置模型服务

可在首次启动前手动创建配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

已有 `.env` 时直接编辑，避免用模板覆盖自己的配置。填写自己的密钥，并核对模型权限：

```dotenv
DASHSCOPE_API_KEY=填写自己的密钥
BAILIAN_HOST=https://dashscope.aliyuncs.com
CHAT_MODEL=qwen3.7-plus
VISION_MODEL=qwen3.7-plus
EMBEDDING_MODEL=text-embedding-v4
RERANK_MODEL=qwen3.7-text-rerank
EMBEDDING_DIMENSIONS=1024
```

修改后停止并重新启动后端。模型名称取决于账户权限，可在 `.env` 中调整。

未配置密钥时，服务、数据源浏览、原件预览、本地关键词检索及受支持的规则问数可以运行；云模型任务会提示配置缺失或降级，不能视为完整问答可用。即使已有文档向量，语义查询仍需要调用服务生成查询向量。

随包向量使用 `text-embedding-v4 / 1024维`。更改正文、嵌入模型、维度或服务地址后，应检查索引状态，必要时显式重建。维护命令见 [数据恢复与索引维护](docs/OHR_DATA_RESTORE.md)。

## 知识库检查与构建

以下命令均在项目根目录执行，使用启动脚本创建的 Python 虚拟环境。若缺少 `backend/.venv/Scripts/python.exe`，先按“快速启动”安装依赖。

### 1. 检查状态和构建目标

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --status
```

查看输出中的 `health` 和 `checkpoint`：

| 字段或提示 | 含义与处理 |
| --- | --- |
| `health.status = ready` | 正文索引可用；继续检查向量状态 |
| `health.status = partial` | 部分文档有解析问题或需要人工核对，查看 `health.issues` |
| `health.status = unavailable` | 正文索引不可用，检查路径、正文文件和索引匹配情况 |
| “索引缺失或版本过期” | 也可能是正文增删、内容变化或解析配置变化导致指纹不匹配，按第2步处理 |
| `health.semantic_index.status = ready` | 向量与当前正文、嵌入模型配置匹配；在线语义查询仍需要有效密钥 |
| `health.semantic_index.status = missing / stale_or_invalid` | 向量缺失或不匹配，需要语义检索时按第3步构建 |
| `checkpoint = null` | 没有向量构建进度记录，不能据此判断正文或已有向量是否可用 |

`--status` 成功退出不代表知识库就绪，请以字段内容为准。网页“刷新状态”读取已有结果，重启服务也不会自动重建整个知识库。

命令行脚本按项目根目录 `.env` 构建，网页选择知识库不会改变脚本的目标。OHR 对应配置为：

```dotenv
DOCUMENTS_PATH=data/public_benchmark/ohr/documents
INDEXES_PATH=data/public_benchmark/ohr/indexes
PARSED_PATH=data/public_benchmark/ohr/parsed
CONFIG_PATH=config/public_benchmark
```

构建其他知识库时，将这些路径改成 `config/datasources.yaml` 中对应知识库的路径，再执行命令。脚本目前没有 `--knowledge-base-id` 参数。PowerShell 中已有的同名环境变量会覆盖 `.env`，请一并核对。

### 2. 恢复正文或构建关键词索引

本目录2026年10月19日排查时，随包索引记录1261份OHR正文（1260份可检索），正文目录实际有1253份 `.ohr.json`，其中8份索引记录对应的正文文件缺失。索引版本和解析配置匹配，但正文指纹不匹配，状态为 `unavailable`。因此，遇到本包这一状态时，先确认这8份正文是否需要恢复。

```powershell
# 核对OHR正文数量；完整基准语料为1261份
(Get-ChildItem -LiteralPath .\data\public_benchmark\ohr\documents -Filter '*.ohr.json' -File).Count
```

- 需要完整OHR语料时，从完整发布包或备份恢复缺失的 `.ohr.json`，保持文件名与内容一致，并保留 `ohr_sources.json` 原件映射，再运行 `--status`。如果正文恢复后匹配已有索引，可以继续使用已有向量。
- 如果文件是通过知识库删除功能移出的，可检查正文目录下 `.deleted/<批次>/manifest.json` 和归档原文件，按文件名恢复到正文目录后再次检查状态。
- 如果删除是有意的，或正文已经修改，备份现有正文和索引后，按当前语料重新构建。恢复PDF不能代替恢复 `.ohr.json` 正文，单纯重建索引也不能找回缺失正文。

构建前先停止本项目服务，结束手动启动的相关服务和其他构建任务。构建期间暂停上传、删除和修改正文，保持语料不变。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\stop.ps1
# 构建或更新正文、切片和BM25关键词索引，保存逐文档检查报告
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --report tmp/knowledge-build-report.json
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --status
```

`stop.ps1` 提示 `No managed Cloud_Agent services are running.` 表示本目录没有受管服务记录；手动启动或从其他目录启动的服务需要在原终端停止。构建命令结束后检查文档数、片段数、`issues` 和报告中的 `health`。完整基准语料有1份空正文，列在 `skipped_sources`，保留其PDF即可。

不带 `--embeddings` 时构建关键词索引；PDF、图片、PPT解析仍可能调用云视觉服务。OHR的 `.ohr.json` 直接使用官方正文。构建还会按当前设置生成引用区域资料，文档数量较多时需要等待命令结束。

原件预览缺失或PDF完整性异常时，可另行核对PDF（此命令会读取全部原件，耗时较长）：

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/verify_ohr_originals.py
```

### 3. 按需构建或续建云向量

先确认正文索引有可用片段，并处理解析失败项。在 `.env` 中配置有效的 `DASHSCOPE_API_KEY`、`BAILIAN_HOST`、`EMBEDDING_MODEL` 和 `EMBEDDING_DIMENSIONS`。完整OHR语料的向量构建会产生云调用费用；仅需关键词检索时可跳过此步。

```powershell
.\backend\.venv\Scripts\python.exe backend/scripts/ingest_documents.py --embeddings --concurrency 2
```

可在另一个PowerShell窗口执行 `--status` 查看进度。`checkpoint.completed` 与 `unique_texts` 对应去重后的文本数，`cached` 是复用的缓存数，因此可能与正文片段数不同。完成后确认 `checkpoint.status = completed` 且 `health.semantic_index.status = ready`。

中断或失败后修复原因，重新执行相同命令，会复用 `INDEXES_PATH` 下 `embedding-cache.sqlite` 中匹配的已完成向量。保留缓存和 `semantic-build.json` 进度文件；`semantic-build.lock` 文件在任务结束后也可能存在，是否仍在构建应检查进程，而非仅看文件是否存在。

| 构建错误 | 处理方式 |
| --- | --- |
| `embedding_configuration_missing` | 补齐密钥和服务地址 |
| `embedding_http_401 / 403` | 核对密钥、模型权限和服务地域 |
| `embedding_http_429` | 降低 `--concurrency`；仍限流时降低 `.env` 中 `EMBEDDING_REQUESTS_PER_SECOND` 后重试 |
| `embedding_timeout_or_connection` | 检查网络、代理和服务地址，修复后续建 |
| `semantic_build_already_running` | 等待已有任务结束，同一索引目录同时运行一个向量构建任务 |
| `corpus_changed_during_embedding_build` | 构建期间正文发生变化，停止修改后重新构建正文索引，再续建向量 |

### 4. 启动并确认网页使用的知识库

```powershell
# 若5174被占用，可使用其他空闲前端端口
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -FrontendPort 5175
# 检查网页所选OHR知识库，而非仅检查后端存活
Invoke-RestMethod 'http://127.0.0.1:8000/api/knowledge/health?knowledge_base_id=ohr' | ConvertTo-Json -Depth 8
```

打开启动脚本输出的网址，选择OHR知识库后刷新状态。自定义知识库使用 `knowledge_base_id=rule_demo_docs`；其正文、解析结果和索引分别在 `data/rule_demo/documents`、`data/rule_demo/parsed`、`data/rule_demo/indexes`。命令行默认构建OHR时，不会同时更新自定义知识库。

## 开始使用

1. 打开网页，新建会话。
2. 问数先选择数据库，知识库问答先选择知识库，跨源任务同时选择两者。缺少来源时系统会要求补充。
3. 选择“Chinook 公开样例（销售）”，尝试“2012年美国的销售额是多少”；使用合成演示库时选择对应规则演示数据源。
4. 知识库选择OHR后，可浏览原文、将文档加入对话，或上传自己的资料。
5. 点击助手回答，在右侧核对SQL、绑定参数、真实引用、原页和执行记录。证据不足或来源冲突时，按提示补充资料与条件。

上传成功的文件会保存到当前知识库，新建会话不会自动清除共享知识库中的文件。会话、附件和执行记录保存在 `data/sessions/`；备份时应连同自己新增的数据一起保存。

界面隐藏“本次查询条件”卡片，后台条件识别、SQL校验和执行记录继续工作。SQL只读，文档正文作为资料与证据。

## 数据和样本

| 路径 | 内容 |
| --- | --- |
| `data/public_benchmark/chinook/` | 公开数据库、完整schema、来源与MIT许可；59位客户、412张发票，年份2009—2013 |
| `data/public_benchmark/ohr/documents/` | 1261份官方校对正文和原件映射 |
| `data/public_benchmark/ohr/pdfs/` | 1261份真实PDF原件 |
| `data/public_benchmark/ohr/indexes/` | 1260份非空正文的96987个片段、真实向量和引用定位资料 |
| `data/rule_demo/` | 10客户、20发票、4退款的独立合成数据库和schema |
| `data/rule_demo/documents/` | 11份合成样本，覆盖MD、TXT、PDF、DOCX |
| `data/documents/` | 合成包生成脚本使用的两份种子文本 |

OHR正文是官方校对文本，不能当作本系统本地OCR效果。1份空正文保留PDF，不建立文字证据。来源与样本说明见 [DATA_SAMPLES.md](submission/DATA_SAMPLES.md)。

索引属于运行数据。遗漏PDF或删除整个 `indexes/` 目录会影响检索、预览或引用定位。Git忽略规则会排除部分大文件，Git源码克隆不等同于本完整发布目录。

## 目录

```text
backend/           应用源码、固定版本依赖、数据维护脚本
frontend/          Vue源码、依赖锁文件、重新构建的dist产物
config/            数据源、指标、实体、schema和检索配置
data/              正式数据、合成样本、原件与有效索引
examples/          可扩展要素的领域配置样例
docs/              数据恢复和索引维护说明
submission/        文档样本与来源、交付文件哈希清单
.env.example       无密钥配置模板
start.ps1          启动与就绪检查
stop.ps1           本目录服务停止脚本
```

## 常见问题

找不到Python或版本不符时，安装Python 3.12并重新打开终端，或指定完整路径：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -PythonPath C:\Python312\python.exe
```

默认后端端口为8000，前端为5174。若提示 `Port 8000 is occupied`，说明已经有程序监听后端端口；`-FrontendPort` 只改变前端端口，不能解决8000冲突。

先查看占用信息：

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in 8000, 5174 } |
    Select-Object LocalAddress, LocalPort, OwningProcess
```

若是此前启动的项目，优先在原终端按 `Ctrl+C`，或在旧项目目录执行 `stop.ps1`。手动运行的uvicorn通常不在启动脚本管理记录中，需核实PID和用途后单独停止。只运行一个项目副本时，释放8000后重新执行启动命令即可。

若仅前端5174被占用，可以选择其他前端端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -FrontendPort 5175
```

后端固定使用8000，两个项目副本不能同时按默认脚本启动。脚本发现冲突会退出，不会擅自结束占用进程。

安装失败时检查包源网络、Python版本和 `tmp/runtime/` 错误日志，修复后重试。主动重新安装依赖：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -InstallDependencies
```

模型失败或索引未就绪时，检查 `.env`、权限、网络和知识库健康状态。更改正文后按维护文档重建匹配索引，并保留实际失败信息。

需要重新构建前端时：

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run build
```

启动脚本使用本地Vite服务。`frontend/dist/` 可供配置静态托管，但仍需将 `/api` 代理到FastAPI；直接打开dist的HTML不能运行完整应用。公网部署需自行配置域名、TLS、访问控制与反向代理。

## 已知边界与交付范围

云模型、视觉OCR、查询向量和重排需要有效模型凭据、网络及模型权限。未配置时不保证完整自然语言问答能力。
复杂公式自动计算、多栏文章归属、低质量扫描和原页定位仍有能力边界；界面中的完成状态不能代替人工核对。
历史扩大回归曾存在失败与环境错误，完整记录及冻结预期保存在原开发档案；本次发布验收只报告实际执行的范围。
历史留出集已被查看，不能称为未见测试。合成检查不代表赛事评分。

产品流程保留会话条件、意图与要素识别、必要澄清、问数/问答/跨源分路、真实执行与证据核验。
SQL只读，答案保留SQL/参数或真实文档来源与执行记录；条件或证据不足时说明缺失或冲突。
文档正文作为资料处理。界面继续隐藏“本次查询条件”卡片，后台识别与执行记录保留。

当前包是Windows本机启动版本，提供Vite开发服务与前端构建产物。公网部署仍需由部署者配置域名、TLS、访问控制和反向代理。
本项目调用云模型，不随包提供本地大模型权重。首次安装需要Python/npm包源网络。

## 验证范围

本次交付已验证新环境安装、前端构建、219项后端工程检查、1261份PDF完整性、真实HTTP接口、只读问数、关键词检索、原件预览、隔离上传及另一位置启动。真实模型问数和独立云视觉页图调用已取得执行结果。

前端12组历史脚本中有5组失败；知识库问题仍可能返回部分回答或证据不足。未完成真实浏览器点击验收，也未重新执行全部历史回归，不能将工程检查等同于所有业务场景通过。历史失败与冻结预期保留，未以修改标准答案消除失败。

文档样本与来源见 [DATA_SAMPLES.md](submission/DATA_SAMPLES.md)，交付文件的SHA-256见 [FILE_MANIFEST.json](submission/FILE_MANIFEST.json)。公开数据的来源与许可随数据目录保留。
