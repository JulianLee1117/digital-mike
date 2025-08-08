import os, sys, json, math, argparse, re, regex
from typing import Iterable, List, Dict, Tuple, Optional
import fitz  # PyMuPDF
import numpy as np
from tqdm import tqdm
import lancedb
from sentence_transformers import SentenceTransformer

# ---------- Config ----------
DEFAULT_TABLE = "israetel_pdf"
DEFAULT_DB_DIR = "./data/lancedb"
MODEL_NAME = "BAAI/bge-small-en-v1.5"  # small, accurate, CPU-friendly

# Chunking configuration (override via env: CHUNK_WORDS, CHUNK_OVERLAP, MIN_CHUNK_WORDS)
CHUNK_WORDS = int(os.getenv("CHUNK_WORDS", "900"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
MIN_CHUNK_WORDS = int(os.getenv("MIN_CHUNK_WORDS", "40"))
INCLUDE_SECTION = os.getenv("INCLUDE_SECTION", "1") == "1" 

# ---------- Text utilities ----------
# Be precise: strip only true headers/footers, not in-body content
HEADER_FOOTER_PATTERNS = [
    r"(?i)scientific principles of strength training",  # book running header
    r"(?i)^contents\b",                                # "Contents"
    r"^P\s*\d+\s*$",                                   # P 10 / P3
    r"^P\d+\s*$",                                      # P10
    r"^\s*(?:Chapter|CHAPTER)\s+(?:No\.\s*)?(?:[A-Z]+|\d+).*$",  # CHAPTER ONE / Chapter No. 1 ...
    r"(?i)^\s*Authors(?:\s+Scientific.*)?\s*$",        # "Authors" or "Authors Scientific Principles..."
    r"(?i)^\s*About the Authors\s*$",                  # About the Authors (running header)
]

WORD_NUM_MAP = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
ROMAN_MAP = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
def roman_to_int(s: str) -> Optional[int]:
    s = s.upper()
    if not re.fullmatch(r"[IVXLCDM]+", s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        val = ROMAN_MAP[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total

def strip_headers_footers(text: str) -> str:
    lines = text.splitlines()
    out = []
    for ln in lines:
        ln_stripped = ln.strip()
        if not ln_stripped:
            continue
        if any(re.search(p, ln_stripped) for p in HEADER_FOOTER_PATTERNS):
            continue
        out.append(ln)
    return "\n".join(out)

def normalize_hyphens_and_spaces(text: str) -> str:
    # join hyphenated linebreaks: "perio-\ndization" -> "periodization"
    text = regex.sub(r"(\p{L})-\n(\p{L})", r"\1\2", text)
    # normalize NBSP and whitespace
    text = text.replace("\u00A0", " ")
    text = regex.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = regex.sub(r"\s{2,}", " ", text).strip()
    return text

SECTION_NAMES = (
    "PREFACE|IMPORTANT TERMS|SPECIFICITY|OVERLOAD|FATIGUE MANAGEMENT|"
    "STIMULUS RECOVERY ADAPTATION|VARIATION|PHASE POTENTIATION|"
    "INDIVIDUAL DIFFERENCE|PERIODIZATION FOR POWERLIFTING|MYTHS.*"
)

CHAPTER_TO_SECTION = {
    1: "Important Terms",
    2: "The Training Principles & What They Mean",
    3: "Specificity",
    4: "Overload",
    5: "Fatigue Management",
    6: "Stimulus Recovery Adaptation",
    7: "Variation",
    8: "Phase Potentiation",
    9: "Individual Difference",
    10: "Periodization For Powerlifting",
    11: "Myths, Fallacies & Fads In Powerlifting",
}

def fuzzy_caps(s: str) -> str:
    # allow optional spaces between letters, spaces as \s+, light tolerance around punctuation
    out = []
    for ch in s:
        if ch.isalpha():
            out.append(f"{re.escape(ch)}\\s*")
        elif ch.isspace():
            out.append("\\s+")
        else:
            out.append(f"\\s*{re.escape(ch)}\\s*")
    return "".join(out)

SECTION_PATTERNS = [
    (title, re.compile(rf"(?mi){fuzzy_caps(title)}"))
    for title in [
        "PREFACE",
        "IMPORTANT TERMS",
        "SPECIFICITY",
        "OVERLOAD",
        "FATIGUE MANAGEMENT",
        "STIMULUS RECOVERY ADAPTATION",
        "VARIATION",
        "PHASE POTENTIATION",
        "INDIVIDUAL DIFFERENCE",
        "PERIODIZATION FOR POWERLIFTING",
        "MYTHS, FALLACIES & FADS IN POWERLIFTING",
    ]
]

def extract_chapter_and_section(raw_page_text: str) -> Tuple[Optional[str], Optional[str]]:
    chap = None
    sec = None

    # Chapter detection that tolerates letter spacing and roman/word numerals
    chap_re = re.compile(
        rf"(?mi){fuzzy_caps('CHAPTER')}\s*(?:No\.\s*)?(?P<num>\d+|[IVXLCDM]+|[A-Z][A-Z\s]+)"
    )
    m = chap_re.search(raw_page_text)
    if m:
        tok = m.group("num").strip()
        chap_n = None
        if tok.isdigit():
            chap_n = int(tok)
        else:
            # remove spaces for word numerals like 'O N E'
            word = tok.replace(" ", "").lower()
            chap_n = WORD_NUM_MAP.get(word)
            if chap_n is None:
                chap_n = roman_to_int(tok.replace(" ", ""))
        chap = f"Chapter {chap_n}" if chap_n else f"Chapter {tok.title()}"

    # Section detection tolerant to letter spacing
    for title, pat in SECTION_PATTERNS:
        if pat.search(raw_page_text):
            sec = title.title()
            break

    return chap, sec

def chunk_by_words(text: str, max_words: int = 900, overlap_words: int = 150) -> List[str]:
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max_words - overlap_words
    i = 0
    while i < len(words):
        chunk_words = words[i:i+max_words]
        if len(chunk_words) >= MIN_CHUNK_WORDS:
            chunks.append(" ".join(chunk_words))
        if i + max_words >= len(words):
            break
        i += step
    return chunks

# ---------- PDF Extraction ----------
def iter_pdf_pages(pdf_path: str) -> Iterable[Tuple[int, str]]:
    doc = fitz.open(pdf_path)
    for page_index in range(len(doc)):
        page = doc[page_index]
        text = page.get_text("text")
        yield page_index + 1, text

# ---------- Ingestion ----------
def ingest_pdf(pdf_path: str, db_dir: str, table: str, force: bool = False) -> Dict:
    os.makedirs(db_dir, exist_ok=True)
    db = lancedb.connect(db_dir)

    if table in db.table_names():
        if not force:
            print(f"[ingest] Table '{table}' already exists in {db_dir}. Use --force to overwrite.")
            return {"skipped": True}
        else:
            print(f"[ingest] Overwriting existing table '{table}'")
            db.drop_table(table)

    print(f"[ingest] Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    rows: List[Dict] = []

    print(f"[ingest] Extracting & chunking: {pdf_path}")
    print(f"[ingest] CHUNK_WORDS={CHUNK_WORDS} CHUNK_OVERLAP={CHUNK_OVERLAP} MIN_CHUNK_WORDS={MIN_CHUNK_WORDS}")

    total_chunks = 0
    last_chap: Optional[str] = None
    last_sec: Optional[str] = None
    source_name = os.path.basename(pdf_path)

    for page_no, raw in tqdm(iter_pdf_pages(pdf_path), total=None):
        if not raw or raw.isspace():
            continue

        # detect metadata BEFORE stripping headers (some headers are informative)
        chap, sec = extract_chapter_and_section(raw)
        if chap:
            last_chap = chap
            # reset section on new chapter
            last_sec = None
        if sec:
            last_sec = sec

        cleaned = normalize_hyphens_and_spaces(strip_headers_footers(raw))
        if not cleaned:
            continue

        page_chunks = chunk_by_words(cleaned, max_words=CHUNK_WORDS, overlap_words=CHUNK_OVERLAP)
        print(f"[page {page_no}] words={len(cleaned.split())} chunks={len(page_chunks)}")
        total_chunks += len(page_chunks)

        # append rows with metadata
        for idx, chunk in enumerate(page_chunks):
            row = {
                "id": f"{source_name}:p{page_no}:c{idx+1}",
                "source": source_name,
                "page": int(page_no),
                "chapter": last_chap,
                "text": chunk,
            }
            if INCLUDE_SECTION:
                row["section"] = last_sec
            rows.append(row)

    print(f"[ingest] total chunks={total_chunks}")

    if not rows:
        print("[ingest] No rows produced. Is the PDF text-selectable?")
        sys.exit(1)

    # Embeddings (batched)
    print(f"[ingest] Embedding {len(rows)} chunks…")
    texts = [r["text"] for r in rows]
    embs = model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=True)
    for r, e in zip(rows, embs):
        r["vector"] = np.asarray(e, dtype=np.float32).tolist()

    # Write to LanceDB
    print(f"[ingest] Writing table '{table}' to {db_dir}")
    tbl = db.create_table(table, data=rows, mode="overwrite")

    # index across versions
    try:
        tbl.create_index(column="vector", metric="cosine")
    except TypeError:
        try:
            tbl.create_index("cosine", "vector")
        except TypeError:
            tbl.create_index(metric="cosine", vector_column_name="vector")

    # Basic stats
    total = tbl.count_rows()
    sample_rows = []
    head_obj = tbl.head(1)
    if hasattr(head_obj, "to_pylist"):  # pyarrow.Table
        sample_rows = head_obj.to_pylist()
    elif hasattr(head_obj, "to_dict"):  # pandas.DataFrame
        sample_rows = head_obj.to_dict(orient="records")
    else:
        print(f"[ingest] Done. rows={total} (no sample_row available)")
        return {"rows": total, "skipped": False}

    if sample_rows:
        slim = [{k: v for k, v in sample_rows[0].items() if k != "vector"}]
        print(f"[ingest] Done. rows={total}, example_row={json.dumps(slim, indent=2)}")
    else:
        print(f"[ingest] Done. rows={total} (no sample_row available)")
    return {"rows": total, "skipped": False}

# ---------- CLI ----------
def parse_args():
    ap = argparse.ArgumentParser(description="Ingest PDF → LanceDB")
    ap.add_argument("--pdf", default=os.environ.get("PDF_PATH", "packages/ingest/Scientific_Principles.pdf"))
    ap.add_argument("--db_dir", default=os.environ.get("DB_DIR", DEFAULT_DB_DIR))
    ap.add_argument("--table", default=os.environ.get("TABLE", DEFAULT_TABLE))
    ap.add_argument("--force", action="store_true", help="Overwrite existing table")
    return ap.parse_args()

if __name__ == "__main__":
    args = parse_args()
    res = ingest_pdf(args.pdf, args.db_dir, args.table, force=args.force)
    if res.get("skipped"):
        sys.exit(0)
