# CoursePilot AI · 课程知识库与学习 Agent

[![自动测试](https://github.com/11181658132/coursepilot-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/11181658132/coursepilot-ai/actions/workflows/ci.yml)

[在线体验](https://coursepilot-ai-ebon.vercel.app/) · [系统架构](docs/architecture.md) · [评测协议](evals/README.md)

CoursePilot AI 是一个基于课程资料的 RAG 问答与学习助手。它不仅返回答案，还展示来源；
学习 Agent 则将资料检索、计划生成、自测反馈、进度管理和用户确认串成闭环。

公开 Demo 只使用 **5 份虚构 DEMO101 资料**，不部署真实课件。私有课程资料通过本地版本使用。
本项目是可运行、可测试的个人开发项目，不将小规模演示包装成成熟的多租户生产系统。

![课程问答与来源引用](docs/assets/coursepilot-chat.png)

## 核心能力

| 模块 | 已实现内容 |
| --- | --- |
| 文档入库 | PDF、PPTX、DOCX、TXT、Markdown 解析；文件哈希去重；增量更新、删除和索引预览 |
| 自建 RAG | 页／幻灯片内切分，BM25 + 向量余弦检索，RRF 融合，可选 LLM 重排序 |
| 证据问答 | 按课程和 lecture／tutorial 筛选；结构化来源 ID、原文引用及页码定位；资料不足时拒答 |
| 学习 Agent | 检索资料 → 生成计划草稿 → 用户确认 → 保存进度 → 提交自测 → 反馈并提议调整 → 再次确认 |
| 状态与安全 | 幂等请求、版本冲突检查、任务恢复、执行后机械检查、确认时重新核对来源与状态 |
| 工程质量 | FastAPI 类型化接口、SQLite／PostgreSQL 持久化、预算预留、限流、测试和 GitHub Actions |
| 可评测性 | 文件级三方案与片段级四方案 RAG 对比、学习任务评测、开发／保留划分、AI 辅助核对与人工审核通道 |

## 系统架构与技术选型

![系统架构](docs/assets/architecture.svg)

- **界面**：Next.js、React、TypeScript。
- **服务**：FastAPI；公开 Demo 通过同源网关访问带签名的 Python Function。
- **模型**：OpenAI Responses API；现有配置为 `gpt-6-luna`。
- **检索**：自建 BM25 + Embedding + RRF；托管 File Search 保留为对照基线。
- **存储**：本地 SQLite 保存索引、对话及任务；公开 Demo 使用独立 PostgreSQL 保存匿名访客计划和配额。

自建检索当前使用精确向量扫描，适合小型课程资料库，尚未实现大规模 ANN／pgvector 检索。
`backend/app` 是权威实现，公开 Demo 只打包白名单模块；自动测试会检查副本是否漂移。

## RAG 评测：效果、速度和成本分开看

评测数据来自版本化的虚构资料：**50 道草稿题，30 道开发题 + 20 道冻结保留题**。
参考答案及来源锚点经过程序校验，但不等于专家金标准。三个对比方案使用同一模型和资料。

### 最近一次完整开发集结果（编号引用修复前）· 2026-10-22

同一批 30 道开发题 × 3 个方案，共 90 条真实模型结果；提供商错误阻塞 0 条，未重抽已完成的失败回答。
以下是评分 v3 的**代理指标，不是语义准确率**：

| 方案 | 代理通过 | 文件级检索命中 | 文件级引用精度 | 关键词召回 | P50／P95（毫秒） | 估算费用（美元） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| File Search | 26/30（86.67%） | 100% | 93.33% | 94.44% | 4,071／5,235 | 0.085909 |
| 混合检索，不重排序 | 24/30（80.00%） | 100% | 88.33% | 93.89% | 2,783／4,793 | 0.005598 |
| 混合检索 + 重排序 | 25/30（83.33%） | 100% | 90.00% | 92.78% | 4,092／5,341 | 0.007574 |

另对全部 90 条回答逐条做了 **AI 辅助核对**：三个方案各有 28/29 条在“正确性与来源支持”两项均通过。
同一道歧义题在三个方案中均保留正确性未知，因此分母为 29，而非将未知计为通过。
这不是人工准确率：File Search 存在资料外细节和引用展示问题；两个混合方案各有一次原文引用校验
失败，安全拦截但未交付答案。自动来源清单也存在遗漏有效支持文件的情况，原始分数未修改。

本轮 180 次 API 请求的预算账本估算合计 **$0.09908068**，未知计费请求为 0，低于单独批准的 $0.50。
金额不含税、存量存储和其他应用流量。重排序增加了本轮延迟与成本，没有显示 AI 核对质量优势；
保留两种方案，不据此宣称普遍结论，也未更改线上默认设置。
详见[本轮评测结果与失败分析](docs/EVALUATION_RESULTS_ZH.md)及[AI 核对方法](docs/AI_REVIEW_ZH.md)。

### 引用失败定向修复 · 2026-10-22

针对上述两个混合方案在同一题上的 `quote_not_found`，新增服务端原文片段目录：
模型选择来源与片段编号，服务端还原原文后继续严格校验，不做模糊匹配或付费自动重试。
修改已同步到本地和公开 Demo 的共享核心，无需重新建立向量索引。

该题的两个方案各做一次真实验收，均返回有效答案与原文引用：**2/2 定向通过**，
5 次 API 请求，估算 **$0.00050774**，未知计费请求 0。它不是整套评测的新通过率，
上面的 90 条历史结果、开发题标签和保留集不变。此后未重跑原 30 题完整评测；下节是新的片段级实验。
详见[引用编号修复、验证和限制](docs/RAG_QUOTE_SELECTION_ZH.md)。

必须区分三种证据：

1. **自动代理指标**：检索命中率、文件级引用精度、关键词覆盖、P50／P95 延迟及估算费用。
2. **AI 辅助核对**：由开发助手逐条阅读问题、回答与虚构原文，保留理由、来源锚点和回答指纹。
   它不是独立人工评审，也不作为专家准确率。
3. **人工评审**：实际审核者核对后亲自提交标签；当前尚无已确认的人工标签，相关人工指标保持空值。

引文确实存在不代表引文支持结论，检索命中也不等于回答正确。
开发集用于诊断，不是独立泛化评测；冻结保留集不参与本轮调优和付费执行。

### 新增片段级四方案实验 · 2026-10-25

新增 **20 份虚构文档、40 个片段、24 道开发题**，包含旧版规则、相似主题、跨课程事实与
不可信指令。四方案使用相同片段正文，96 条真实模型回答全部完成，不替换旧实验成绩。

| 方案 | 片段 Recall@3（22 道有答案题） | AI 核对回答正确（24 题） | P50／P95（毫秒） |
| --- | ---: | ---: | ---: |
| BM25 | 61.36% | 17/24 | 1,681／2,757 |
| 纯向量 | 93.18% | 23/24 | 1,851／2,502 |
| 混合 RRF | 86.36% | 22/24 | 1,793／2,848 |
| 混合 + 重排序 | 95.45% | 24/24 | 5,052／5,873 |

两道无答案题单独核对，各方案均恰当拒答；不将它们记为检索召回满分。
AI 核对由开发助手完成，**不是人工准确率**。来源主要为英文、查询含中英文，草稿片段标签也有
遗漏可用支持的局限。本轮重排序改善了开发题，但代价是延迟与成本；线上默认设置不变。
计时不含缓存的查询 Embedding，不能视作生产端到端性能。新 8 道和原 20 道保留题均未执行。

另跑 6 个 Agent 场景：4 个语义任务交付 3 个，2 个受控故障检查组通过。
概念颠倒题虽然被模型识别，调整却不针对弱点且引用错误，已被拦截；该失败原样保留。
不能把安全检查通过当作学习任务成功。全部 100 条语义记录已做 AI 辅助核对，人工标签仍为 0。

本轮方法预跑及正式实验共同估算 **$0.04345686／267 次请求**，未知计费 0，
低于两项共同授权的 $0.50。预跑输入不对称的结果未用于上表，但费用完整计入。
详见[结果、成本与失败分析](docs/RAG_AGENT_CHALLENGE_RESULTS_ZH.md)及[方法与复现](docs/RAG_AGENT_CHALLENGE_ZH.md)。

2026-10-26 已针对 Agent 反馈失败补上引文编号选择、薄弱点与调整理由关联，以及
“保留有效反馈、拦截无效调整”的降级处理，前端会明确提示，仍须人工确认才能应用调整。
本次仅离线回归，新增 API 费用为 0；未重写上述历史成绩或宣称模型语义问题已全部解决。
详见[修复、验证与剩余限制](docs/AGENT_FEEDBACK_REPAIR_ZH.md)。

## 学习 Agent：可执行，但不替用户决定

Agent 只能调用允许的资料查询与草稿提议工具，不能执行任意代码，也不能自行启用计划或修改进度。

- 草稿必须满足天数、任务结构、来源和字面引文检查；失败时不发布可执行任务。
- 用户明确确认后，服务端再次核对当前证据和版本，在事务中启用计划或应用调整。
- 自测反馈明确标注不确定性，不作为标准评分；不确定时不能提出计划调整。
- 只允许修改尚未完成的后续任务；并发进度变化会使旧建议失效。
- 确认、进度更新和中断恢复不调用模型；生成计划与反馈可能产生费用。
- 支持删除计划及自测内容；必要的预算、配额和防重放记录保留，不通过删除重置额度。

**机械检查不证明学习目标被充分覆盖，也不证明反馈语义正确。** 用户仍需审阅任务与证据。
详见[执行后检查](docs/AGENT_POSTCHECKS.md)、[反馈闭环](docs/STUDY_FEEDBACK.md)、
[恢复机制](docs/AGENT_RECOVERY.md)和[删除边界](docs/STUDY_DELETION.md)。

已有的 30/30 Agent 脚本评测测试真实状态机与安全分支，但使用模拟模型和固定检索，
不是实际模型任务成功率。另有虚构资料的真实模型小规模功能验收，不能据此声称模型准确率。
协议与限制见 [Agent 评测说明](evals/AGENT_EVALUATION.md)。

## 本地启动

需要 Python 3.12、Node.js 20+；CI 使用 Node.js 22。以下命令从项目根目录开始。

### 1. 安装后端并配置环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env
```

在本机编辑 `.env`，设置 `OPENAI_API_KEY` 和 `KNOWLEDGE_BASE_DIR`，不要把密钥粘贴到代码或 Git。
如使用自建引擎，设置 `RAG_ENGINE=hybrid`。推荐的资料目录：

```text
knowledge-base/
├── DEMO101/
│   ├── lecture/
│   └── tutorial/
└── YOUR_COURSE/
    ├── lecture/
    └── tutorial/
```

课程名动态发现；名称含 `tute` 或 `tutorial` 的文件也归为 tutorial。
本地私有资料的解析文本会在索引／提问时发送到配置的模型服务，请先确认处理权限。

### 2. 启动 API、索引 Worker 和前端

在三个终端分别运行：

```bash
# 终端一：项目根目录，使用已激活的虚拟环境
uvicorn app.main:app --app-dir backend --reload --port 8000
```

```bash
# 终端二：项目根目录
PYTHONPATH=backend .venv/bin/python -m app.worker
```

```bash
# 终端三：项目根目录
cd frontend
cp .env.local.example .env.local
npm ci
npm run dev
```

打开 [本地页面](http://localhost:3000)，接口文档在 [FastAPI 文档](http://localhost:8000/docs)。
本地管理接口目前是单用户设计，不要未经认证直接暴露到公网。

### 3. 建立索引

在“资料管理”中预览后创建索引任务。默认上传只保存文件；勾选“上传后建立索引”才会入队。
上传期间已有扫描在运行时，会保留后续扫描任务，不能只看到上传成功就认为已入库。
任务一直排队时，检查 Worker 是否运行且使用同一配置。

也可使用命令行：

```bash
cd backend
../.venv/bin/python -m app.cli sync --engine hybrid --dry-run
../.venv/bin/python -m app.cli sync --engine hybrid
```

预览只读本地哈希，不调用 API；实际索引会发送文本进行 Embedding，可能产生费用。
File Search 基线可通过 `--engine file_search` 使用。

## 入库可靠性与来源版本

索引任务使用 SQLite 持久化、事务认领和单主机写锁，显示阶段、尝试次数、失败和已记录成本。
暂时失败遵守 Retry-After，尝试最多三次；配额、账单、认证等问题停止等待处理。
相同内容重复上传复用文件，同名不同内容返回冲突，不覆盖现有资料。

单份文档完成解析、Embedding 和新鲜度校验后才原子替换。失败保留旧索引，但已变化的来源在
重新索引前不作为当前证据返回。历史引用附带文档版本；重新打开时会提示来源变化或不可用。

这是**单主机、逐文档原子更新**，不是分布式队列、全资料库快照或外部调用恰好付费一次。
服务在付费调用后、落库前崩溃仍可能造成重复调用；本地成本记录不是提供商账单。
详见[入库生命周期](docs/INGESTION_LIFECYCLE.md)。

## 公开 Demo 的数据与费用边界

- 公开资料只读；不提供私有课程上传或本地管理接口。
- API Key、数据库连接和签名密钥只在服务端，私有课件、对话、报告和遥测不进入 Git。
- RAG 使用 PostgreSQL 持久化配额：每网络地址每天最多 5 次，全球每 UTC 月最多 300 次；
  重启或清 Cookie 不重置计数。数据库异常时停止模型调用。
- Agent 使用签名匿名访客隔离和独立预算预留，默认估算上限为每次 $0.05、全球每天 $0.10、
  每月 $0.50。原 RAG 请求费用与托管费用不包含在 Agent 预算中。
- 匿名会话不是正式账号认证；清理 Cookie 或换浏览器会失去原会话入口。
- 请求限额和成本估算不等于账单硬上限；还需检查模型及托管平台的账户级费用控制。

公开 Python Function 冷启动从虚构预计算索引恢复临时 SQLite，不会重新给整批文档生成向量。
部署根目录为 `frontend`；修改共享核心后执行：

```bash
node frontend/scripts/prepare-rag.mjs
```

完整的数据库、环境变量及验收步骤见[公开 Agent 部署](docs/PUBLIC_AGENT_DEPLOYMENT.md)和
[RAG 配额说明](docs/RAG_RATE_LIMITS.md)。不要仅设置演示开关就认为具备可用的数据库和模型配置。

## 测试与评测复现

免费检查：

```bash
PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v
.venv/bin/python -m ruff check backend/app backend/tests frontend/api backend/scripts/export_demo_index.py
PYTHONPATH=backend .venv/bin/python -m app.benchmark --validate
PYTHONPATH=backend .venv/bin/python -m app.agent_evaluation
node frontend/scripts/prepare-rag.mjs --check
cd frontend
npm test
npm run typecheck
npm run build
```

PostgreSQL 集成测试在 CI 的一次性测试数据库上执行；没有指定测试库时本地跳过。
不要将破坏性的集成测试指向公开 Demo 数据库。

真实三方案评测会收费，必须先确认预算。使用新的目录，不覆盖历史实验：

```bash
PYTHONPATH=backend .venv/bin/python -m app.benchmark \
  --live --budget-usd 0.50 --output-dir evals/reports/your-new-experiment
```

相同实验目录和指纹可续跑未完成项；不要为了追求通过而重复生成已完成的失败回答。
没有预算授权时只运行 `--validate`。原始回答、证据、标签及数据库保留在忽略的本地目录中。

## 已知限制与后续方向

- 图片扫描件需要 OCR；当前不凭空生成页码或将空文本当作成功入库。
- 小型虚构语料的文件级命中率很容易达到高分，不能证明大语料或真实课程效果。
- 引用存在性、机械检查和单元测试通过，不等于模型语义可靠。
- 重排序有额外延迟和费用；是否值得开启应由对比结果决定。
- 正式多用户产品仍需账号认证、完整权限模型、备份恢复及负载测试。
- 下一步应根据真实失败案例选择单一改动，再在独立实验中回归；不以堆叠更多工具代替评测。

## 文档导航

- [系统设计与取舍](docs/architecture.md)
- [RAG 加固](docs/RAG_HARDENING.md)
- [引用编号修复与定向验收](docs/RAG_QUOTE_SELECTION_ZH.md)
- [RAG 评测协议](evals/README.md)
- [三方案文件级评测结果](docs/EVALUATION_RESULTS_ZH.md)
- [片段级四方案与学习任务评测方法](docs/RAG_AGENT_CHALLENGE_ZH.md)
- [最新片段级结果、成本与失败分析](docs/RAG_AGENT_CHALLENGE_RESULTS_ZH.md)
- [AI 辅助核对方法与边界](docs/AI_REVIEW_ZH.md)
- [人工审核操作指南](docs/HUMAN_REVIEW_ZH.md)
- [RAG 错误分析流程](docs/RAG_QUALITY_WORKFLOW.md)
- [Agent 评测协议](evals/AGENT_EVALUATION.md)
- [Agent 反馈与针对性调整修复](docs/AGENT_FEEDBACK_REPAIR_ZH.md)
