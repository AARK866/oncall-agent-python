# Workflow validation and LangGraph runtime

Editable workflow drafts are validated before they can call an LLM, retrieve
knowledge, or execute an operations tool. A valid draft is compiled into a
native LangGraph graph at runtime.

## Validation contract

The validator requires:

- exactly one `start` and one `end` node;
- unique node and edge IDs;
- edge endpoints that reference existing nodes;
- no self-loops, duplicate paths, or cycles;
- every node reachable from `start` and able to reach `end`;
- no incoming edge on `start` and no outgoing edge on `end`;
- valid type-specific node configuration;
- supported workflow variable types and boolean `required` flags.

Invalid drafts remain editable, but `POST /draft/run` returns HTTP `422` with a
structured validation report. Free-form edge conditions are currently rejected;
they are never evaluated with Python `eval`.

Validate without executing:

```text
POST /api/workflow-apps/{app_id}/draft/validate
```

## Node runtime

The compiler maps control-plane nodes to existing project services:

- `tool` calls the governed `ToolRegistry` and its retry policy;
- `knowledge_retrieval` calls the ACL-aware `KnowledgeAgent` retriever;
- `agent` calls the configured LangChain-compatible LLM client;
- `human_review` creates a native LangGraph interrupt;
- `start` exposes workflow inputs and `end` selects the final output.

Tool, prompt, query, and output configuration supports safe value references:

```json
{
  "tool_name": "query_metrics",
  "arguments": {
    "service": "${inputs.service}",
    "window": "30m"
  }
}
```

References can read `inputs` or earlier `node_outputs`. Missing paths fail with
a configuration error; arbitrary code is not executed.

## Execution

```text
POST /api/workflow-apps/{app_id}/draft/run
```

Request:

```json
{
  "inputs": {
    "question": "payment-api 5xx is increasing",
    "service": "payment-api"
  },
  "thread_id": "optional-stable-thread-id"
}
```

LangGraph state uses reducers for `node_outputs` and `trace`, allowing
unconditional parallel branches to merge without overwriting each other. The
response reports the draft revision, runtime, trace, outputs, and whether the
graph stopped at a human-review interrupt. Persistent review decisions and run
history are added in the observability and approval phase.
