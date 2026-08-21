"""
Local text embeddings via sentence-transformers.

all-mpnet-base-v2, 768 dimensions. Local rather than an API: embeddings run on
every ingested item and every question, so an API would be the one per-token
cost that scales with usage rather than with value. The model is ~420MB on disk
and needs no key, no quota, and no network at inference time.
"""

import asyncio
import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

EMBED_DIMS = settings.embedding_dimensions
_MAX_CHARS = 2000          # ~500 tokens; the model truncates at 384 anyway

# Encoding is CPU-bound and releases the GIL inside torch, so a dedicated
# single-thread executor keeps it off the event loop without letting several
# encodes contend for the same cores.
_executor: "ThreadPoolExecutor | None" = None


def _get_executor():
    global _executor
    if _executor is None:
        from concurrent.futures import ThreadPoolExecutor

        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embed")
    return _executor


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer

    logger.info("Loading embedding model %s (first call only)…", settings.embedding_model)
    model = SentenceTransformer(settings.embedding_model)

    actual = model.get_sentence_embedding_dimension()
    if actual != EMBED_DIMS:
        # A dimension mismatch writes vectors the pgvector column rejects, one
        # confusing row-level error at a time. Fail at load instead.
        raise RuntimeError(
            f"Embedding model {settings.embedding_model} produces {actual}-dim vectors "
            f"but the schema expects {EMBED_DIMS}. Either set EMBEDDING_MODEL back, "
            f"or migrate items.embedding to vector({actual}) and re-embed everything."
        )
    logger.info("Embedding model ready (%d dims)", actual)
    return model


def _clean(text: str) -> str:
    return " ".join((text or "").split())[:_MAX_CHARS]


async def warm_up() -> None:
    """
    Load the model at startup rather than on the first user question.

    Cold load takes several seconds; paying it during boot keeps it out of a
    student's first request.
    """
    try:
        await asyncio.get_running_loop().run_in_executor(_get_executor(), _get_model)
    except Exception as exc:
        logger.error("Embedding model failed to load: %s", exc)


async def embed_text(text: str) -> list[float]:
    """Embed one string. Normalised, so cosine distance is a dot product."""
    cleaned = _clean(text)
    if not cleaned:
        return [0.0] * EMBED_DIMS

    def _encode() -> list[float]:
        return _get_model().encode(cleaned, normalize_embeddings=True).tolist()

    return await asyncio.get_running_loop().run_in_executor(_get_executor(), _encode)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed many strings in one forward pass — far faster than N single calls.

    Empty inputs keep their slot as a zero vector so the returned list always
    aligns positionally with the input.
    """
    if not texts:
        return []

    cleaned = [_clean(t) for t in texts]
    non_empty = [(i, t) for i, t in enumerate(cleaned) if t]
    if not non_empty:
        return [[0.0] * EMBED_DIMS for _ in texts]

    def _encode() -> list[list[float]]:
        vectors = _get_model().encode(
            [t for _, t in non_empty],
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    encoded = await asyncio.get_running_loop().run_in_executor(_get_executor(), _encode)

    results: list[list[float]] = [[0.0] * EMBED_DIMS for _ in texts]
    for (index, _), vector in zip(non_empty, encoded):
        results[index] = vector
    return results
