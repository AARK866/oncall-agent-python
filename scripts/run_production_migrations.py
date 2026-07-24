import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command
from alembic.config import Config

from app.agents.langgraph_checkpointing import create_langgraph_checkpointer
from app.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run application and LangGraph production migrations."
    )
    parser.add_argument(
        "--skip-langgraph",
        action="store_true",
        help="Run only Alembic migrations.",
    )
    args = parser.parse_args()
    if not settings.database_url:
        parser.error("DATABASE_URL is required")

    alembic_config = Config(str(PROJECT_ROOT / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "migrations"),
    )
    command.upgrade(alembic_config, "head")
    print("- Alembic schema: PASS")

    if not args.skip_langgraph:
        _, name = create_langgraph_checkpointer("postgres")
        print(f"- LangGraph checkpointer ({name}): PASS")

    print("Production migrations: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
