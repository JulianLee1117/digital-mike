# Ingestion Notes (PDF → LanceDB)

**Source:** `packages/ingest/Scientific_Principles.pdf`  
**Output:** `./data/lancedb` table `israetel_pdf`

## Chunking
- Window: 900 words, overlap 150 (`CHUNK_WORDS`, `CHUNK_OVERLAP`)
- Min chunk: 40 words (`MIN_CHUNK_WORDS`)
- Preprocessing:
  - Strip running headers/footers (book title, “Contents”, page labels)
  - Fix hyphenated line breaks (`perio-\ndization` → `periodization`)
  - Collapse whitespace/NBSP
- Metadata per row: `{id, source, page, chapter, section?, text, vector}`  
  - `chapter` via tolerant regex (also handles roman numerals/letter-spaced text)
  - `section` optional (e.g., Specificity, Overload …)

## Embeddings
- Model: `BAAI/bge-small-en-v1.5`
- Normalization: `normalize_embeddings=True` (cosine)
- Index: `create_index(column="vector", metric="cosine")`

## Rebuild
```bash
PDF_PATH=packages/ingest/Scientific_Principles.pdf \
DB_DIR=./data/lancedb \
TABLE=israetel_pdf \
python packages/ingest/ingest.py --force
