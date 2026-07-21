# LangGraph Setup

The project now has two graph runtimes:

- `local`: built-in graph workflow. This is the default and needs no extra setup.
- `langgraph`: real LangGraph runtime using `StateGraph`.

## 1. Install Dependencies

```powershell
pip install -r requirements.txt
```

If your project path contains spaces, use the activated virtual environment:

```powershell
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## 2. Keep Local Runtime

Use this while learning or when dependency installation is not ready.

```env
OPS_GRAPH_RUNTIME=local
```

Run:

```powershell
python -m pytest
```

## 3. Switch To LangGraph Runtime

After `langgraph` is installed, edit `.env`:

```env
OPS_GRAPH_RUNTIME=langgraph
OPS_GRAPH_CHECKPOINTER=memory
```

Then start the API:

```powershell
python -m uvicorn app.main:app --reload
```

Send a request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/incidents/analyze `
  -ContentType "application/json" `
  -Body '{"message":"payment service 5xx error rate is high","session_id":"langgraph-test","mode":"ops"}'
```

Check response metadata:

```text
metadata.graph_runtime.used
metadata.graph_runtime.checkpointer_used
metadata.graph_trace
```

If `used` is `langgraph` and `checkpointer_used` is `memory`, the request ran
through LangGraph with its native checkpointer interface enabled.

## 4. Safe Fallback

You can also use:

```env
OPS_GRAPH_RUNTIME=auto
```

In `auto` mode, the app tries LangGraph first. If `langgraph` is not installed or fails at runtime, it falls back to the local graph workflow.

## 5. Checkpointer Modes

```env
OPS_GRAPH_CHECKPOINTER=memory
```

Supported values:

- `memory`: LangGraph `MemorySaver`, process-local;
- `auto`: use memory when available;
- `disabled`: run LangGraph without a native checkpointer.
