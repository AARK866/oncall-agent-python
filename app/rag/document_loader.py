from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawDocument:
    doc_id: str
    title: str
    content: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_markdown_documents(directory: str | Path) -> list[RawDocument]:
    base_dir = Path(directory)
    if not base_dir.exists():
        return []

    documents: list[RawDocument] = []
    for path in sorted(base_dir.rglob("*.md")):
        content = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(base_dir).as_posix()
        documents.append(
            RawDocument(
                doc_id=relative_path,
                title=_extract_title(content) or path.stem,
                content=content,
                source=str(path),
                metadata={"path": relative_path},
            )
        )
    return documents


def _extract_title(content: str) -> str | None:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return None
