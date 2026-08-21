"""
Text embedding using sentence-transformers (local, free, no API key).
Model: all-mpnet-base-v2 → 768-dim vectors, strong semantic quality.
Switched from all-MiniLM-L6-v2 (384-dim) for better retrieval accuracy.
"""

import asyncio
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

EMBED_MODEL = "all-mpnet-base-v2"
EMBED_DIMS  = 768


@lru_cache(maxsize=1)
def _get_model():
    from sentence_transformers import SentenceTransformer
    logger.info(f"Loading embedding model {EMBED_MODEL} (first call only)...")
    return SentenceTransformer(EMBED_MODEL)


async def embed_text(text: str) -> list[float]:
    """Return 768-dim embedding vector. Runs model in thread pool (it's sync)."""
    text = text.replace("\n", " ").strip()[:2000]

    def _encode():
        model = _get_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    return await asyncio.get_event_loop().run_in_executor(None, _encode)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed multiple texts at once — faster than calling embed_text N times."""
    cleaned = [t.replace("\n", " ").strip()[:2000] for t in texts]

    def _encode_batch():
        model = _get_model()
        return model.encode(cleaned, normalize_embeddings=True).tolist()

    return await asyncio.get_event_loop().run_in_executor(None, _encode_batch)
