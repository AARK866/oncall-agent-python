# LangChain / LangGraph / Milvus / MCP 接入规划

当前项目已经完成一个本地可运行的 OnCall Agent MVP：

```text
FastAPI
  -> ConversationAgent
  -> KnowledgeAgent / OpsAgent
  -> Local RAG / Mock Ops Tools
  -> ReAct / Plan-Execute-Replan
  -> ChatResponse / SSE
```

下一阶段目标不是推倒重写，而是把本地实现逐步替换成更接近生产和简历项目的技术栈：

- LangChain：替换文档加载、切分、RAG chain、工具封装。
- LangGraph：替换手写 ReAct / Plan-Execute-Replan 编排。
- Milvus：替换本地关键词检索。
- MCP：替换部分本地工具调用协议，接入外部工具服务。

## 1. 当前实现与目标技术栈对应关系

| 当前模块 | 当前实现 | 目标替换方向 |
| --- | --- | --- |
| `app/rag/document_loader.py` | 手写 markdown loader | LangChain `DocumentLoader` |
| `app/rag/splitter.py` | 手写 markdown splitter | LangChain `TextSplitter` |
| `app/rag/retriever.py` | 本地关键词检索 | Milvus + LangChain Retriever |
| `app/agents/react_loop.py` | 手写 ReAct 流程 | LangGraph 状态图 |
| `app/agents/plan_execute.py` | 手写 Plan-Execute-Replan | LangGraph 多节点图 |
| `app/tools/base.py` | 本地 Tool 协议 | LangChain Tool / MCP Tool |
| `app/tools/mock_ops_tools.py` | 本地 mock JSON | Prometheus / Loki / GitLab / CMDB / MCP |
| `app/llm/client.py` | Mock + OpenAI-compatible client | LangChain ChatModel adapter |
| `app/api/chat.py` | FastAPI + SSE | 保留 |
| `app/schemas.py` | Pydantic DTO | 保留 |

## 2. 推荐接入顺序

### 阶段 A：LangChain 文档处理

目标：

- 保留 `LocalKnowledgeBase` 对外接口。
- 内部先换成 LangChain 文档加载和切分。

改动范围：

```text
app/rag/document_loader.py
app/rag/splitter.py
requirements.txt
tests/test_rag.py
```

新增依赖：

```text
langchain
langchain-community
```

验收标准：

```text
pytest 通过
payment_5xx.md 仍然能被检索命中
KnowledgeAgent 不需要改调用方式
```

### 阶段 B：Milvus 向量库

目标：

- 新增 `MilvusKnowledgeBase`。
- 保留当前 `LocalKnowledgeBase`，作为本地 fallback。

新增模块建议：

```text
app/rag/vector_store.py
app/rag/embeddings.py
app/rag/milvus_retriever.py
```

新增配置：

```env
VECTOR_STORE=local
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_COLLECTION=oncall_runbooks
EMBEDDING_MODEL=...
```

验收标准：

```text
VECTOR_STORE=local 时仍然使用本地检索
VECTOR_STORE=milvus 时写入并检索 Milvus
KnowledgeAgent 仍然只依赖统一 search(query, top_k) 接口
```

### 阶段 C：LangGraph 编排 ReAct

目标：

- 将 `ReactLoop` 改成 LangGraph 状态图。
- 显式建模 `thought`、`action`、`observation`、`final`。

建议状态：

```text
OpsGraphState
  - question
  - service
  - messages
  - next_action
  - tool_results
  - react_steps
  - final_report
```

建议节点：

```text
analyze_question
select_tool
execute_tool
observe
should_continue
finalize
```

验收标准：

```text
OpsAgent.analyze() 返回结构不变
metadata["react_steps"] 仍然存在
SSE 仍能展示 thinking / tool_call / tool_result / final
```

### 阶段 D：LangGraph 编排 Plan-Execute-Replan

目标：

- 将 `PlanExecuteReplan` 改成 LangGraph 状态图。
- Planner、Executor、Replanner 拆成独立节点。

建议节点：

```text
planner
executor
replanner
final_report
```

验收标准：

```text
metadata["plan_trace"] 仍然存在
计划步骤、执行结果、replan notes 能被 API 返回
```

### 阶段 E：MCP 工具接入

目标：

- 保留 `ToolRegistry`。
- 新增 MCP adapter，把 MCP 工具包装成当前项目的 `BaseTool`。

建议新增模块：

```text
app/tools/mcp_adapter.py
app/tools/mcp_client.py
```

设计原则：

```text
Agent 不直接依赖 MCP
Agent 仍然只调用 ToolRegistry
MCP 工具通过 adapter 变成 BaseTool
```

验收标准：

```text
本地 mock 工具和 MCP 工具可以同时注册
ToolCall / ToolResult 格式保持不变
```

## 3. 为什么不要一次性全接

LangChain、LangGraph、Milvus、MCP 都很有价值，但一次性引入会带来很多变量：

```text
依赖安装
本地服务启动
向量维度
模型 API Key
图状态设计
工具协议
错误处理
测试稳定性
```

当前项目已经有一个清晰的本地闭环，最稳的方式是：

```text
每次只替换一个底层实现
保持上层接口稳定
每一步都跑 pytest
每一步单独 commit
```

## 4. 后续提交建议

```text
Add LangChain document processing
Add Milvus vector store adapter
Add LangGraph ReAct workflow
Add LangGraph plan execute replan workflow
Add MCP tool adapter
```

## 5. 当前保留的稳定接口

后续改造时，优先保持这些接口不变：

```text
KnowledgeAgent.answer(question, session_id, top_k) -> ChatResponse
OpsAgent.analyze(question, session_id, service) -> ChatResponse
ToolRegistry.execute(tool_call) -> ToolResult
LocalKnowledgeBase.search(query, top_k) -> list[SourceDocument]
POST /api/chat -> ChatResponse
POST /api/chat/stream -> SSE events
```

只要这些接口稳定，底层从 mock / local 替换成真实系统时，上层 API 和测试就不会大面积重写。
