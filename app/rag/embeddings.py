import hashlib
import math
from typing import Any
from typing import Protocol

from app.config import settings
from app.rag.text_features import tokenize_text


class EmbeddingModel(Protocol):
    def embed(self, text: str) -> list[float]:
        """Convert text to a numeric vector."""


class HashEmbeddingModel:
    """Small deterministic local embedding model for development."""

    def __init__(self, dimensions: int = 128) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be greater than 0")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = tokenize_text(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], byteorder="big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class LangChainEmbeddingModel:
    """Production embedding model backed by LangChain OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        dimensions: int | None = None,
        request_dimensions: bool = False,
        tiktoken_enabled: bool = False,
        check_ctx_length: bool = False,
        timeout_seconds: int = 30,
        max_retries: int = 6,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else None
        self.dimensions = dimensions
        self.request_dimensions = request_dimensions
        self.tiktoken_enabled = tiktoken_enabled
        self.check_ctx_length = check_ctx_length
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._model: Any | None = None

    def embed(self, text: str) -> list[float]:
        return self._embedding_model().embed_query(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embedding_model().embed_documents(texts)

    def _embedding_model(self):
        if self._model is not None:
            return self._model

        try:
            from langchain_openai import OpenAIEmbeddings
        except ImportError as exc:
            raise RuntimeError(
                "Real embedding mode requires langchain-openai. "
                "Install dependencies with: pip install -r requirements.txt"
            ) from exc

        kwargs: dict[str, Any] = {
            "model": self.model,
            "api_key": self.api_key,
            "timeout": self.timeout_seconds,
            "max_retries": self.max_retries,
            "tiktoken_enabled": self.tiktoken_enabled,
            "check_embedding_ctx_length": self.check_ctx_length,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.request_dimensions and self.dimensions:
            kwargs["dimensions"] = self.dimensions

        self._model = OpenAIEmbeddings(**kwargs)
        return self._model


def create_embedding_model() -> EmbeddingModel:
    provider = settings.embedding_provider.lower().strip()
    if provider in {"hash", "local", "mock"}:
        return HashEmbeddingModel(dimensions=settings.embedding_dimensions)

    if provider in {"langchain", "langchain-openai", "openai", "openai-compatible", "qwen"}:
        if not settings.embedding_api_key:
            raise ValueError(
                "EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER uses a real embedding service."
            )
        return LangChainEmbeddingModel(
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            base_url=settings.embedding_base_url,
            dimensions=settings.embedding_dimensions,
            request_dimensions=settings.embedding_request_dimensions,
            tiktoken_enabled=settings.embedding_tiktoken_enabled,
            check_ctx_length=settings.embedding_check_ctx_length,
            timeout_seconds=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )

    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {settings.embedding_provider}")
