"""Build and search local Qwen3 embedding vectors for Semantic Retrieval."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from .store import open_db

VECTOR_SCHEMA_VERSION = 1
DEFAULT_EMBEDDING_MODEL = "text-embedding-qwen3-embedding-4b"
DEFAULT_LM_BASE_URL = "http://127.0.0.1:6001/v1"
_CHUNK_SIZE = 4096


def default_embedding_model() -> str:
    return os.environ.get("DTK_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def default_lm_base_url() -> str:
    return os.environ.get("DTK_LLM_BASE_URL", DEFAULT_LM_BASE_URL)


class EmbeddingClient:
    """Minimal OpenAI-compatible `/v1/embeddings` client for LM Studio."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = (base_url or default_lm_base_url()).rstrip("/")
        self.model = model or default_embedding_model()
        self.timeout = timeout
        self.api_key = os.environ.get("DTK_LLM_API_KEY")

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, 0), dtype=np.float32)

        payload = {"model": self.model, "input": list(texts)}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"embedding request failed ({error.code}): {detail}") from error

        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError(
                f"embedding endpoint returned {len(rows) if isinstance(rows, list) else 'invalid'} rows"
            )
        vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            raise RuntimeError("embedding endpoint returned an invalid matrix")
        norms = np.linalg.norm(vectors, axis=1)
        if np.any(norms == 0):
            raise RuntimeError("embedding endpoint returned a zero vector")
        return vectors / norms[:, None]

    def embed_query(self, text: str) -> np.ndarray:
        vector = self.embed([text])
        return vector[0]


@dataclass
class VectorIndex:
    """L2-normalized vectors keyed by Derived Store tag IDs."""

    tag_ids: np.ndarray
    matrix: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tag_ids = np.asarray(self.tag_ids)
        self.matrix = np.asarray(self.matrix)
        if self.tag_ids.ndim != 1 or self.matrix.ndim != 2:
            raise ValueError("tag_ids must be 1-D and matrix must be 2-D")
        if len(self.tag_ids) != len(self.matrix) or len(self.tag_ids) == 0:
            raise ValueError("tag_ids and matrix must have the same non-zero length")
        # Duplicate tag_ids are allowed: one Tag may have many semantic documents.
        if not np.issubdtype(self.tag_ids.dtype, np.integer):
            raise ValueError("tag_ids must contain integers")

    @classmethod
    def load(cls, path: str | Path) -> "VectorIndex":
        with np.load(Path(path), allow_pickle=False) as archive:
            required = {"tag_ids", "matrix", "metadata_json"}
            if not required.issubset(archive.files):
                raise ValueError(f"invalid vector index: missing {sorted(required - set(archive.files))}")
            metadata = json.loads(str(archive["metadata_json"].item()))
            if metadata.get("dtk_vector_schema_version") != VECTOR_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported vector schema: {metadata.get('dtk_vector_schema_version')}"
                )
            index = cls(archive["tag_ids"], archive["matrix"], metadata)
        if index.metadata.get("dimensions") != index.matrix.shape[1]:
            raise ValueError("vector metadata dimensions do not match matrix")
        return index

    def save(self, path: str | Path) -> None:
        destination = Path(path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".tmp")
        temporary.unlink(missing_ok=True)
        with temporary.open("wb") as handle:
            np.savez(
                handle,
                tag_ids=self.tag_ids,
                matrix=self.matrix,
                metadata_json=np.array(
                    json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)
                ),
            )
        temporary.replace(destination)

    def find(self, query_vector: np.ndarray, limit: int = 100) -> list[tuple[int, float]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        vector = np.asarray(query_vector, dtype=np.float32)
        if vector.ndim != 1 or vector.shape[0] != self.matrix.shape[1]:
            raise ValueError("query vector dimensions do not match the index")
        norm = float(np.linalg.norm(vector))
        if norm == 0 or not np.isfinite(norm):
            raise ValueError("query vector must be finite and non-zero")
        vector = vector / norm

        scores = np.empty(len(self.tag_ids), dtype=np.float32)
        for start in range(0, len(self.tag_ids), _CHUNK_SIZE):
            end = min(start + _CHUNK_SIZE, len(self.tag_ids))
            # Stored vectors are float16 to keep the index compact; accumulate
            # dot products in float32 chunks to avoid float16 rounding drift.
            chunk = self.matrix[start:end].astype(np.float32, copy=False)
            scores[start:end] = chunk @ vector

        count = min(limit, len(scores))
        if count == len(scores):
            top = np.arange(count)
        else:
            top = np.argpartition(-scores, count - 1)[:count]
        ranked = sorted(
            ((int(self.tag_ids[position]), float(scores[position])) for position in top),
            key=lambda item: (-item[1], item[0]),
        )
        return ranked


def semantic_document(name: str, wiki: str | None, aliases: Sequence[str]) -> str:
    """Build one retrieval document from official name, aliases, and Wiki."""
    parts = [name.replace("_", " ")]
    parts.extend(alias.replace("_", " ") for alias in aliases)
    if wiki:
        parts.append(wiki[:2000])
    return "\n".join(parts)


def build_vector_index(
    db_path: str | Path,
    output_path: str | Path,
    client: EmbeddingClient,
    min_post_count: int = 100,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Embed high-usage General/Meta Tags into an atomic `.npz` index."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if min_post_count < 0:
        raise ValueError("min_post_count must not be negative")

    conn = open_db(db_path)
    try:
        tag_rows = conn.execute(
            """
            SELECT id, name, wiki
            FROM tags
            WHERE type IN ('general', 'meta') AND post_count >= ?
            ORDER BY id
            """,
            (min_post_count,),
        ).fetchall()
        if not tag_rows:
            raise ValueError("no tags match the M2 vector index criteria")

        aliases: dict[int, list[str]] = {}
        for row in conn.execute(
            """
            SELECT a.tag_id, a.alias
            FROM aliases a
            JOIN tags t ON t.id = a.tag_id
            WHERE t.type IN ('general', 'meta') AND t.post_count >= ?
            ORDER BY a.tag_id, a.alias
            """,
            (min_post_count,),
        ):
            aliases.setdefault(row["tag_id"], []).append(row["alias"])
    finally:
        conn.close()

    texts = [
        semantic_document(row["name"], row["wiki"], aliases.get(row["id"], []))
        for row in tag_rows
    ]
    vectors: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors.append(client.embed(batch))
        if progress:
            progress(min(start + len(batch), len(texts)), len(texts))

    matrix = np.concatenate(vectors, axis=0).astype(np.float16)
    index = VectorIndex(
        tag_ids=np.asarray([row["id"] for row in tag_rows], dtype=np.int64),
        matrix=matrix,
        metadata={
            "dtk_vector_schema_version": VECTOR_SCHEMA_VERSION,
            "kind": "canonical_tag_semantic_v1",
            "model": client.model,
            "dimensions": int(matrix.shape[1]),
            "tag_count": int(matrix.shape[0]),
            "min_post_count": int(min_post_count),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    index.save(output_path)
    return dict(index.metadata)


def build_research_vector_index(
    db_path: str | Path,
    output_path: str | Path,
    client: EmbeddingClient,
    batch_size: int = 32,
) -> dict[str, Any]:
    """Embed published research documents without rebuilding the official index."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    conn = open_db(db_path)
    try:
        table_exists = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'research_documents'
            """
        ).fetchone()
        if table_exists is None:
            raise ValueError("no published research documents found")
        rows = conn.execute(
            """
            SELECT rd.tag_id, rd.kind, rd.language, rd.text, t.name
            FROM research_documents rd
            JOIN tags t ON t.id = rd.tag_id
            ORDER BY rd.tag_id, rd.kind, rd.language, rd.text
            """
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError("no published research documents found")

    texts = [
        f"{row['name'].replace('_', ' ')}\n{row['kind']} {row['language']}\n{row['text']}"
        for row in rows
    ]
    vectors: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        vectors.append(client.embed(texts[start : start + batch_size]))
    matrix = np.concatenate(vectors, axis=0).astype(np.float16)
    index = VectorIndex(
        tag_ids=np.asarray([row["tag_id"] for row in rows], dtype=np.int64),
        matrix=matrix,
        metadata={
            "dtk_vector_schema_version": VECTOR_SCHEMA_VERSION,
            "kind": "published_research_documents_v1",
            "model": client.model,
            "dimensions": int(matrix.shape[1]),
            "document_count": int(matrix.shape[0]),
            "tag_count": int(len({row["tag_id"] for row in rows})),
            "built_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    index.save(output_path)
    return dict(index.metadata)
