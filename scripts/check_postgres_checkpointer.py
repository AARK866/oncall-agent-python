import argparse
import sys
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.langgraph_checkpointing import create_langgraph_checkpointer
from app.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify LangGraph PostgreSQL checkpoint persistence."
    )
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or DATABASE_URL is required")
    settings.database_url = args.database_url

    from langgraph.graph import END, StateGraph

    builder = StateGraph(dict)
    builder.add_node(
        "increment",
        lambda state: {"value": int(state["value"]) + 1},
    )
    builder.set_entry_point("increment")
    builder.add_edge("increment", END)

    checkpointer, checkpointer_name = create_langgraph_checkpointer("postgres")
    graph = builder.compile(checkpointer=checkpointer)
    thread_id = f"checkpoint-acceptance-{uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke({"value": 1}, config)
    persisted = graph.get_state(config)
    passed = (
        checkpointer_name == "postgres"
        and result.get("value") == 2
        and persisted.values.get("value") == 2
    )

    print("LangGraph PostgreSQL checkpointer acceptance")
    print(f"- checkpointer: {checkpointer_name}")
    print(f"- graph result: {result.get('value')}")
    print(f"- persisted value: {persisted.values.get('value')}")
    print(f"- status: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
