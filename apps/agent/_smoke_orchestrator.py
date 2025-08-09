import os, json
from orchestrator import Orchestrator

def main():
    missing = [k for k in ("LLM_API_KEY", "MODEL_NAME") if not os.getenv(k)]
    if missing:
        print("WARNING: missing env:", missing)

    orch = Orchestrator()

    q1 = "What’s MRV for intermediate bench?"
    a1, m1 = orch.answer(q1)
    print("\nRAG TEST:")
    print("Q:", q1)
    print("A:", a1)
    print("META:", m1)

    q2 = "Macros for 200g cooked chicken breast"
    a2, m2 = orch.answer(q2)
    print("\nNUTRITION TEST:")
    print("Q:", q2)
    print("A:", a2)
    print("META:", json.dumps(m2, indent=2))

if __name__ == "__main__":
    main()
