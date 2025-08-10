import json
import os
from pathlib import Path
from dotenv import load_dotenv
from .nutritionix import lookup_macros, summarize_for_speech

# Load env from apps/agent/.env regardless of CWD
AGENT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(AGENT_DIR / ".env")

def main():
    q = "200g cooked chicken breast"
    items = lookup_macros(q)
    print("RAW ITEMS:\n", json.dumps(items, indent=2))
    print("\nSPOKEN SUMMARY:\n", summarize_for_speech(items))

if __name__ == "__main__":
    # Quick sanity check that env is set
    for k in ("NUTRITIONIX_APP_ID", "NUTRITIONIX_API_KEY"):
        if not os.getenv(k):
            print(f"WARNING: {k} not set in env.")
    main()
