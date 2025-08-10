import os
from typing import List, Dict, Optional, Any

import numpy as np
import lancedb
from sentence_transformers import SentenceTransformer

from ..utils.logging import get_logger
logger = get_logger(__name__)


DEFAULT_DB_DIR = os.getenv("DB_DIR", "./data/lancedb")
DEFAULT_TABLE = os.getenv("TABLE", "israetel_pdf")
# Must match ingestion model + normalization
DEFAULT_EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")


def _mmr(
    query_vec: np.ndarray,
    doc_vecs: np.ndarray,
    k: int = 5,
    lambda_mult: float = 0.5,
) -> List[int]:
    """
    Maximal Marginal Relevance: select indices balancing relevance and diversity.
    Assumes vectors are L2-normalized; uses cosine via dot products.
    """
    if doc_vecs.shape[0] == 0:
        return []

    selected: List[int] = []
    candidates = list(range(doc_vecs.shape[0]))

    # precompute relevances (cosine)
    rel = (doc_vecs @ query_vec).astype(float)

    while len(selected) < min(k, len(candidates)):
        best_i = None
        best_score = -1e9
        for i in candidates:
            if not selected:
                div = 0.0
            else:
                # max similarity to already selected
                div = max(float(doc_vecs[i] @ doc_vecs[j]) for j in selected)
            score = lambda_mult * rel[i] - (1.0 - lambda_mult) * div
            if score > best_score:
                best_score = score
                best_i = i
        selected.append(best_i)
        candidates.remove(best_i)
    return selected


class RAGStore:
    """
    Thin LanceDB wrapper with:
      - same embedder as ingestion (bge-small-en-v1.5, normalized)
      - vector search -> fetch_k
      - MMR re-rank -> top-k distinct chunks
    """
    def __init__(
        self,
        db_dir: str = DEFAULT_DB_DIR,
        table: str = DEFAULT_TABLE,
        embed_model: str = DEFAULT_EMBED_MODEL,
    ):
        self.db_dir = db_dir
        self.table_name = table
        self.db = lancedb.connect(db_dir)
        try:
            self.tbl = self.db.open_table(table)
        except Exception as e:
            raise RuntimeError(f"Could not open LanceDB table '{table}' in '{db_dir}': {e}")

        # Must match ingestion’s model+normalization
        self.embedder = SentenceTransformer(embed_model)
        self._norm = True  # ingestion used normalize_embeddings=True

    def _encode(self, text: str) -> np.ndarray:
        vec = self.embedder.encode([text], normalize_embeddings=self._norm)[0]
        return np.asarray(vec, dtype=np.float32)

    def search(
        self,
        query: str,
        k: int = 5,
        fetch_k: Optional[int] = None,
        where: Optional[str] = None,
        include_fields: Optional[List[str]] = None,
        lambda_mult: float = 0.55,
        dedupe_on: Optional[List[str]] = None,
        min_score: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns list of dicts with at least {text, page, chapter, section?, id?}.
        - k: final results after MMR
        - fetch_k: how many to pull from ANN before MMR (default 8*k)
        - where: optional LanceDB filter expression
        - include_fields: extra fields to include in results
        - lambda_mult: MMR relevance/diversity balance (0..1)
        - dedupe_on: fields to dedupe by (default ["page","text"])
        """
        # Default: moderate candidate pool before MMR for a good precision/recall balance
        if fetch_k is None:
            fetch_k = max(k * 8, 16)
        if include_fields is None:
            include_fields = ["id", "text", "page", "chapter", "section"]
        if dedupe_on is None:
            dedupe_on = ["page", "text"]

        qv = self._encode(query)

        # Vector search
        q = self.tbl.search(qv).metric("cosine")
        if where:
            q = q.where(where)
        # Lance returns full records incl. 'vector'; we’ll keep what we need
        hits = q.limit(fetch_k).to_list()

        if not hits:
            return []

        # Optional dedupe before MMR (cheap guard)
        def sig(rec: Dict[str, Any]) -> str:
            return "||".join(str(rec.get(f, "")) for f in dedupe_on)

        seen = set()
        uniq = []
        for h in hits:
            s = sig(h)
            if s in seen:
                continue
            seen.add(s)
            uniq.append(h)

        # Build doc matrix for MMR and cosine similarities
        doc_vecs = np.stack([np.asarray(h["vector"], dtype=np.float32) for h in uniq])
        rel = (doc_vecs @ qv).astype(float)

        # Select top-k diverse docs using MMR based on vector similarity only
        keep_idx = _mmr(qv, doc_vecs, k=k, lambda_mult=lambda_mult)
        selected = [uniq[i] for i in keep_idx]

        # Build output with cosine scores; apply optional min_score gate
        out: List[Dict[str, Any]] = []
        for i in keep_idx:
            r = uniq[i]
            slim = {f: r.get(f) for f in include_fields if f in r}
            slim["text"] = slim.get("text", "")
            slim["page"] = int(slim.get("page", -1))
            slim["chapter"] = slim.get("chapter")
            # cosine similarity as score for logging/inspection
            slim["cosine"] = float(rel[i])
            slim["score"] = float(rel[i])
            if (min_score is not None) and (slim["score"] < min_score):
                continue
            out.append(slim)
        # Keep MMR-selected order; optionally you can sort by score if desired

        # emit debug log
        level_debug = os.getenv("RAG_DEBUG", "0") == "1"
        if level_debug and out:
            logger.debug("rag.candidates", extra={
                "extra": {
                    "query": query[:200],
                    "k": k, "returned": len(out), "lambda_mult": lambda_mult, "min_score": min_score,
                    "top_score": round(out[0]["score"], 3),
                    "top_cosine": round(out[0].get("cosine", -1.0), 3),
                    "top": [
                        {"page": r["page"], "chapter": r["chapter"], "score": round(r["score"], 3)}
                        for r in out[:5]
                    ],
                }
            })
        return out
