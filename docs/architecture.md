# Python OnCall Agent 架构设计

本文档基于公开项目介绍，先定义 Python 版本的整体框架、运行流程和模块职责。目标不是逐字复刻原项目源码，而是搭建一个同类型、可落地、可扩展的智能 OnCall Agent 系统蓝图。

## 1. 项目定位

Python 版本定位为一个面向运维排障场景的大模型 Agent 应用。

核心能力：

- 知识库问答：基于企业文档、故障手册、SOP、FAQ 做 RAG 检索增强回答。
- 多轮对话：支持用户连续追问，保留会话上下文。
- 告警分析：接收告警信息，自动查询相关日志、指标和知识库。
- 根因推理：通过 ReAct 或 Plan-Execute-Replan 流程拆解问题、调用工具、汇总结论。
- 处理建议：输出排查步骤、风险提示和可执行的修复建议。
- 流式响应：通过 SSE 返回 Agent 推理和执行过程。
- 工具扩展：通过 Function Calling / Tool Registry / MCP 风格接口接入外部系统。

## 2. 总体架构

```text
用户 / 前端 / 告警平台
        |
        v
FastAPI API 层
        |
        v
会话层 Conversation Service
        |
        v
Agent 编排层 Agent Orchestrator
        |
        +-------------------+
        |                   |
        v                   v
知识库 Agent          运维 Agent
        |                   |
        v                   v
RAG 检索层            工具调用层
        |                   |
        v                   v
向量库 / 文档库       日志 / 指标 / 告警 / 发布 / 服务拓扑
        \                   /
         \                 /
          v               v
           大模型调用层 LLM Client
                    |
                    v
              响应生成 / SSE 输出
```

可以把系统分成五层：

| 层级 | 职责 |
| --- | --- |
| 接入层 | 提供 HTTP API、SSE 流式接口、健康检查、告警 Webhook |
| 会话层 | 管理多轮对话、消息历史、用户上下文、任务状态 |
| Agent 编排层 | 判断任务类型，选择 Agent，控制 ReAct 或 Plan-Execute-Replan 流程 |
| 能力层 | 提供 RAG、工具调用、Prompt、模型调用、记忆、权限控制 |
| 数据与外部系统层 | 连接文档、向量库、日志系统、监控系统、告警平台、CMDB 等 |

## 3. 核心 Agent 设计

### 3.1 对话 Agent

职责：

- 作为用户入口，理解用户意图。
- 判断问题属于知识问答、告警排查、普通对话还是操作请求。
- 管理多轮上下文。
- 将任务转交给知识库 Agent 或运维 Agent。
- 汇总多个 Agent 的结果并生成最终回答。

输入：

- 用户问题
- 会话 ID
- 历史消息
- 可选告警上下文

输出：

- 最终自然语言回答
- 需要流式展示的中间步骤
- 结构化诊断结果

典型问题：

- “支付服务 5xx 升高怎么办？”
- “帮我分析这个告警。”
- “Redis 连接数打满一般怎么处理？”

### 3.2 知识库 Agent

职责：

- 接收用户问题或运维 Agent 的子问题。
- 对文档库做向量检索和关键词检索。
- 整理命中文档片段。
- 基于文档上下文生成可靠回答。
- 给出引用来源，降低幻觉。

输入：

- 查询问题
- 文档过滤条件，例如系统名、业务线、文档类型
- top_k 检索数量

输出：

- 基于知识库的回答
- 命中文档片段
- 文档来源和相似度

涉及模块：

- Document Loader
- Text Splitter
- Embedding Client
- Vector Store
- Retriever
- RAG Chain

### 3.3 运维 Agent

职责：

- 面向告警和故障排查。
- 根据告警内容制定排查计划。
- 调用日志、指标、服务拓扑、发布记录等工具。
- 结合知识库结果进行根因分析。
- 输出处理建议和下一步动作。

输入：

- 告警标题
- 告警级别
- 服务名
- 时间范围
- 指标信息
- 用户补充说明

输出：

- 根因假设
- 已执行的排查步骤
- 工具调用结果摘要
- 置信度
- 修复建议
- 是否需要人工介入

典型工具：

- query_metrics：查询 QPS、错误率、延迟、CPU、内存等指标
- query_logs：查询异常日志
- query_alerts：查询相关告警
- query_deployments：查询最近发布记录
- query_service_topology：查询上下游依赖
- query_runbook：查询故障手册

## 4. Agent 控制流程

### 4.1 普通知识问答流程

```text
用户提问
  |
  v
API 接收请求
  |
  v
Conversation Service 加载历史
  |
  v
对话 Agent 判断为知识问答
  |
  v
知识库 Agent 检索相关文档
  |
  v
RAG 生成答案
  |
  v
保存会话记录
  |
  v
返回回答
```

适用场景：

- 故障手册查询
- 技术方案解释
- SOP 问答
- FAQ 问答

### 4.2 ReAct 排障流程

ReAct 用于“边思考边调用工具”的场景。

```text
收到告警 / 用户问题
  |
  v
Thought：分析当前信息缺口
  |
  v
Action：选择一个工具
  |
  v
Observation：读取工具结果
  |
  v
Thought：更新判断
  |
  v
是否足够得出结论？
  |
  +-- 否：继续 Action / Observation
  |
  +-- 是：生成最终诊断
```

示例：

```text
Thought: 支付服务 5xx 升高，需要先确认错误率和开始时间。
Action: query_metrics(service="payment", metric="http_5xx_rate")
Observation: 10:05 后错误率从 0.2% 升到 8.7%。
Thought: 需要检查同时间是否有发布。
Action: query_deployments(service="payment", since="10:00")
Observation: 10:03 发布 payment-api v2.8.1。
Thought: 故障与发布高度相关，需要查询错误日志。
Action: query_logs(service="payment", keyword="ERROR", since="10:03")
Observation: 大量数据库连接超时。
Final: 初步判断 v2.8.1 引入数据库连接池配置问题。
```

### 4.3 Plan-Execute-Replan 排障流程

Plan-Execute-Replan 用于复杂问题，先规划，再执行，过程中根据结果调整计划。

```text
用户问题 / 告警
  |
  v
Planner 生成排查计划
  |
  v
Executor 执行第一个步骤
  |
  v
收集 Observation
  |
  v
Replanner 判断计划是否需要调整
  |
  +-- 继续执行下一步
  |
  +-- 修改计划
  |
  +-- 结束并输出结论
```

示例计划：

```text
1. 查询服务错误率和延迟趋势。
2. 查询同时间窗口的异常日志。
3. 查询最近发布记录。
4. 查询上下游依赖是否有异常。
5. 检索相关故障手册。
6. 汇总根因和处理建议。
```

## 5. 模块划分

建议 Python 工程结构：

```text
app/
  main.py
  config.py
  schemas.py
  api/
    chat.py
    alerts.py
    knowledge.py
    health.py
  agents/
    conversation_agent.py
    knowledge_agent.py
    ops_agent.py
    planner.py
    executor.py
    replanner.py
    react_loop.py
  rag/
    document_loader.py
    splitter.py
    embeddings.py
    vector_store.py
    retriever.py
    rag_chain.py
  tools/
    registry.py
    base.py
    metrics_tool.py
    logs_tool.py
    alerts_tool.py
    deployments_tool.py
    topology_tool.py
    runbook_tool.py
  llm/
    client.py
    prompts.py
    output_parser.py
  memory/
    conversation_memory.py
    task_memory.py
  services/
    chat_service.py
    alert_service.py
    knowledge_service.py
    stream_service.py
  data/
    runbooks/
    mock_metrics.json
    mock_logs.json
    mock_alerts.json
  observability/
    tracing.py
    logging.py
  security/
    permissions.py
    audit.py
tests/
  test_rag.py
  test_tools.py
  test_ops_agent.py
```

### 5.1 API 层

模块：

- `api/chat.py`
- `api/alerts.py`
- `api/knowledge.py`
- `api/health.py`

职责：

- 暴露 REST API。
- 暴露 SSE 流式接口。
- 接收告警 Webhook。
- 做基础参数校验。
- 不写复杂业务逻辑，只调用 Service。

核心接口：

| 接口 | 方法 | 职责 |
| --- | --- | --- |
| `/api/chat` | POST | 普通对话问答 |
| `/api/chat/stream` | POST | SSE 流式对话 |
| `/api/alerts/analyze` | POST | 告警分析 |
| `/api/knowledge/search` | POST | 知识库检索 |
| `/health` | GET | 健康检查 |

### 5.2 Service 层

模块：

- `services/chat_service.py`
- `services/alert_service.py`
- `services/knowledge_service.py`
- `services/stream_service.py`

职责：

- 组织业务用例。
- 调用 Agent。
- 保存会话记录。
- 处理异常和超时。
- 将 Agent 中间事件转换成 SSE 事件。

### 5.3 Agent 编排层

模块：

- `agents/conversation_agent.py`
- `agents/knowledge_agent.py`
- `agents/ops_agent.py`
- `agents/react_loop.py`
- `agents/planner.py`
- `agents/executor.py`
- `agents/replanner.py`

职责：

- 决定使用哪个 Agent。
- 维护 Agent 状态。
- 控制工具调用顺序。
- 生成结构化诊断结论。

核心状态对象：

```text
AgentState
  - session_id
  - user_input
  - intent
  - messages
  - alert_context
  - plan
  - observations
  - tool_calls
  - retrieved_docs
  - final_answer
```

### 5.4 RAG 层

模块：

- `rag/document_loader.py`
- `rag/splitter.py`
- `rag/embeddings.py`
- `rag/vector_store.py`
- `rag/retriever.py`
- `rag/rag_chain.py`

职责：

- 加载文档。
- 切分文档。
- 生成 embedding。
- 写入向量库。
- 根据问题检索相关片段。
- 拼装 Prompt 并调用模型生成回答。

第一阶段可以用本地内存向量库实现，后续替换为 Milvus。

抽象边界：

```text
VectorStore
  - add_documents(docs)
  - similarity_search(query, top_k)
```

只要保持这个接口，底层可以从内存实现切换到 Milvus。

### 5.5 工具调用层

模块：

- `tools/base.py`
- `tools/registry.py`
- `tools/*_tool.py`

职责：

- 定义工具统一协议。
- 注册可用工具。
- 给 Agent 提供工具 schema。
- 执行工具调用。
- 捕获工具异常。
- 记录工具调用日志。

工具统一输入输出：

```text
ToolInput
  - name
  - arguments
  - trace_id

ToolResult
  - tool_name
  - success
  - data
  - error
  - elapsed_ms
```

第一阶段工具可以读取本地 mock 数据。

第二阶段再接入真实平台：

- Prometheus / Grafana
- Elasticsearch / Loki
- Alertmanager
- Kubernetes
- CMDB
- GitLab / Jenkins / ArgoCD
- 内部工单系统

### 5.6 LLM 层

模块：

- `llm/client.py`
- `llm/prompts.py`
- `llm/output_parser.py`

职责：

- 屏蔽不同模型供应商差异。
- 管理普通生成、流式生成、工具调用。
- 维护 Prompt 模板。
- 解析模型输出为结构化数据。

建议接口：

```text
LLMClient
  - generate(messages, tools=None)
  - stream(messages, tools=None)
  - generate_json(messages, schema)
```

可接入：

- OpenAI API
- DeepSeek
- 通义千问
- 本地模型

### 5.7 Memory 层

模块：

- `memory/conversation_memory.py`
- `memory/task_memory.py`

职责：

- 保存多轮对话消息。
- 保存一次排障任务的中间状态。
- 控制历史消息长度。
- 摘要压缩长期上下文。

第一阶段可以使用内存字典。

后续可以替换为：

- Redis
- SQLite
- PostgreSQL

### 5.8 Observability 层

模块：

- `observability/logging.py`
- `observability/tracing.py`

职责：

- 记录每次请求。
- 记录 Agent 每一步 Thought、Action、Observation。
- 记录工具调用耗时。
- 记录模型调用 token 和耗时。
- 支持后续回放和问题定位。

这部分对 Agent 项目很重要，因为 Agent 行为必须可解释、可追踪。

### 5.9 Security 层

模块：

- `security/permissions.py`
- `security/audit.py`

职责：

- 控制哪些工具允许自动执行。
- 区分只读工具和写操作工具。
- 高风险操作需要人工确认。
- 记录审计日志。

建议策略：

| 工具类型 | 示例 | 是否自动执行 |
| --- | --- | --- |
| 只读查询 | 查日志、查指标、查告警 | 可以 |
| 低风险操作 | 创建工单、发送通知 | 可配置 |
| 高风险操作 | 回滚、扩容、重启服务 | 需要人工确认 |

## 6. 请求与事件模型

### 6.1 ChatRequest

```text
ChatRequest
  - session_id: string
  - message: string
  - mode: auto | knowledge | ops
  - stream: boolean
```

### 6.2 AlertAnalyzeRequest

```text
AlertAnalyzeRequest
  - alert_id: string
  - title: string
  - severity: critical | warning | info
  - service: string
  - start_time: string
  - labels: object
  - annotations: object
```

### 6.3 AgentEvent

SSE 流式输出建议统一成事件：

```text
AgentEvent
  - event: thinking | tool_call | tool_result | retrieved_docs | answer_delta | final | error
  - data: object
```

示例：

```text
event: thinking
data: {"text": "正在检查 payment 服务最近 30 分钟错误率"}

event: tool_call
data: {"tool": "query_metrics", "arguments": {"service": "payment"}}

event: tool_result
data: {"tool": "query_metrics", "summary": "10:05 后 5xx 明显升高"}

event: final
data: {"answer": "初步判断与 10:03 发布相关..."}
```

## 7. 数据流

### 7.1 知识库构建数据流

```text
Markdown / PDF / HTML / TXT 文档
  |
  v
Document Loader
  |
  v
Text Splitter
  |
  v
Embedding Client
  |
  v
Vector Store
  |
  v
Retriever 对外提供检索能力
```

### 7.2 告警分析数据流

```text
告警 Webhook
  |
  v
Alert Service
  |
  v
Ops Agent
  |
  +--> query_metrics
  +--> query_logs
  +--> query_deployments
  +--> query_service_topology
  +--> Knowledge Agent / RAG
  |
  v
根因分析报告
```

## 8. MVP 实现顺序

建议分四个阶段实现。

### 阶段一：本地可运行骨架

目标：

- FastAPI 能启动。
- `/api/chat` 可返回普通回答。
- `/api/knowledge/search` 可查询本地文档。
- 工具层用 mock 数据。

模块：

- API 层
- Service 层
- Memory 内存实现
- 简单 LLM Client
- 本地 RAG
- Tool Registry

### 阶段二：Agent 排障闭环

目标：

- 运维 Agent 能根据告警自动调用工具。
- 支持 ReAct 循环。
- 支持结构化输出根因分析。

模块：

- Ops Agent
- React Loop
- Metrics Tool
- Logs Tool
- Deployments Tool
- Runbook Tool

### 阶段三：复杂任务编排

目标：

- 支持 Plan-Execute-Replan。
- 支持 SSE 展示每一步。
- 支持多 Agent 协作。

模块：

- Planner
- Executor
- Replanner
- Stream Service
- AgentEvent

### 阶段四：真实系统接入

目标：

- 向量库切换到 Milvus。
- 日志接入 Elasticsearch 或 Loki。
- 指标接入 Prometheus。
- 告警接入 Alertmanager。
- 增加权限和审计。

模块：

- MilvusVectorStore
- PrometheusMetricsTool
- LokiLogsTool
- AlertmanagerTool
- Permission Service
- Audit Log

## 9. 最小闭环示例

用户输入：

```text
payment 服务 5xx 突然升高，帮我分析原因
```

系统流程：

```text
1. Chat API 接收请求。
2. Conversation Agent 判断为运维排障。
3. Ops Agent 创建排查任务。
4. 查询 payment 的 5xx 指标趋势。
5. 查询 payment 最近错误日志。
6. 查询 payment 最近发布记录。
7. 检索知识库中的 5xx 故障处理手册。
8. 汇总观察结果。
9. 生成根因判断和处理建议。
10. 通过 SSE 返回每一步过程和最终结论。
```

最终输出结构：

```text
诊断结论：
  初步判断 payment 服务 5xx 升高与 10:03 的 v2.8.1 发布高度相关。

证据：
  - 10:05 后 5xx 从 0.2% 升至 8.7%。
  - 同时间窗口出现大量数据库连接超时日志。
  - 10:03 存在 payment-api v2.8.1 发布记录。

建议：
  - 优先回滚 v2.8.1。
  - 检查数据库连接池配置。
  - 观察回滚后 5xx 和延迟是否恢复。
  - 若无法回滚，临时扩容数据库连接池并限制上游流量。

风险：
  - 回滚属于高风险操作，需要人工确认。
```

## 10. 后续实现原则

- 先做可运行闭环，再替换真实基础设施。
- 先实现只读工具，再考虑写操作工具。
- Agent 的每一步都要可观测、可回放。
- Prompt、工具 schema、状态对象要结构化。
- RAG 必须返回引用来源。
- 高风险操作必须经过权限判断和人工确认。
- 模块之间通过抽象接口连接，避免把模型、向量库、监控系统写死。
