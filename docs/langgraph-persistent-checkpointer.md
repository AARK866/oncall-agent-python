# LangGraph persistent checkpointer

This step prepares the graph for durable LangGraph checkpoints.

## What Changed

The app now sends JSON-friendly snapshots into LangGraph instead of passing the
`OpsGraphState` Python object directly.

```text
OpsGraphState -> dict snapshot -> LangGraph checkpoint
dict snapshot -> OpsGraphState -> node execution
```

That matters because durable checkpointers need state that can survive process
restart, package upgrades, and stricter serializers.

## SQLite Checkpointer

Install the optional package:

```powershell
.\.venv\Scripts\python.exe -m pip install langgraph-checkpoint-sqlite
```

Then configure:

```env
OPS_GRAPH_RUNTIME=langgraph
OPS_GRAPH_CHECKPOINTER=sqlite
OPS_GRAPH_CHECKPOINT_DB_PATH=app/data/langgraph_checkpoints.sqlite
```

Run:

```powershell
.\.venv\Scripts\python.exe scripts\run_acceptance.py
```

Expected:

```text
8 passed, 0 failed
```

## Current Backends

- `memory`: in-process LangGraph `MemorySaver`, good for tests and local demos.
- `sqlite`: file-backed LangGraph checkpointer, good for local durable resume.
- `disabled`: compile LangGraph without native checkpoint persistence.

The app still writes readable task checkpoints to its own SQLite task tables.
Those records are used by task APIs and audits. LangGraph checkpoints are the
runtime resume mechanism.

## Production Note

SQLite is not a shared cluster backend. For multiple API or worker instances,
use a shared durable checkpointer such as Postgres when the matching LangGraph
package is installed.
