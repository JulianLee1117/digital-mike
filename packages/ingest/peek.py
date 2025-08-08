import lancedb, random

DB_DIR = "./data/lancedb"
TABLE = "israetel_pdf"

db = lancedb.connect(DB_DIR)
tbl = db.open_table(TABLE)

print("rows:", tbl.count_rows())

# Load all rows (small table) without pandas
head = tbl.head(10000)  # > total rows (374)
data = head.to_pylist() if hasattr(head, "to_pylist") else head.to_dict(orient="records")

for r in random.sample(data, k=min(5, len(data))):
    meta = {k: r.get(k) for k in ("id", "page", "chapter", "section")}
    print(f"\n{meta}")
    t = str(r.get("text") or "")
    print(t[:300].replace("\n"," ") + ("…" if len(t)>300 else ""))
