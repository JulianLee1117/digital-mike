import os
from orchestrator import Orchestrator

def main():
    missing = [k for k in ("LLM_API_KEY", "MODEL_NAME") if not os.getenv(k)]
    if missing:
        print("WARNING: missing env:", missing)
    orch = Orchestrator()
    # 1) Short pep talk
    reply, meta = orch.answer("Give me a one-sentence bench day pep talk.")
    print("PEP TALK:\n", reply, "\nMETA:", meta)

    # 2) Style check
    reply, meta = orch.answer("I'm plateaued at 185 on bench. Any quick advice?")
    print("\nADVICE:\n", reply)

if __name__ == "__main__":
    main()
