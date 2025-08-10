### Digital Mike Israetel — Real‑Time RAG Voice Agent

- by Julian Lee

Digital Mike is a real‑time strength‑coach voice agent you can talk to in your browser. It runs on LiveKit Cloud for low‑latency audio, transcribes speech live, retrieves evidence from a strength‑training book via LanceDB, and answers with a concise, coach‑style voice. It also performs a nutrition macro lookup via the Nutritionix API when you ask about calories or macros.

This repo contains:
- Backend Python agent (LiveKit Agent + FastAPI token service + agent joiner)
- React frontend with a live transcript and minimal controls
- RAG pipeline over a PDF (“Scientific Principles of Strength Training”) using LanceDB
- A simple ingestion tool to build the LanceDB table from the PDF

### High‑level design

- LiveKit Cloud hosts an audio room. The browser connects and publishes mic audio.
- The Python agent joins the same room. Its pipeline is:
  - STT: OpenAI Whisper/4o via LiveKit plugin
  - VAD + turn detection: Silero VAD + multilingual turn detector
  - LLM: OpenAI `gpt-4o-mini` (configurable)
  - TTS: ElevenLabs
- After each user turn, the agent runs a RAG search against LanceDB to retrieve relevant book snippets. It injects a compact RAG context and an “answer contract” into the LLM system messages and requests a short, speech‑first reply. When useful, the reply cites chapter and page (e.g., “based on chapter 5 page 23 in my book”).
- When users ask about calories/macros, the LLM calls the Nutritionix tool. Tool progress/results are streamed to the client via LiveKit data packets on topic `tool.events`.
- The frontend shows a live transcript for both parties and a subtle “speaking” ring on Mike’s avatar.

### Repo layout

- `apps/agent/`
  - `main.py`: Agent class (`DigitalMike`), RAG hook, and standalone LiveKit worker entrypoint
  - `agent_service.py`: FastAPI service that connects to LiveKit and hosts an agent session per room
  - `token_server.py`: FastAPI server that mints user tokens and asks the agent service to join a fresh room
  - `persona.py`: Mike’s voice/personality guardrails
  - `rag/store.py`: LanceDB wrapper + MMR re‑ranker
  - `utils/logging.py`: simple JSON/pretty logging setup
  - `tools/nutritionix.py`: Nutritionix macro lookup + speech summary
  - `requirements.txt`: backend deps
- `apps/frontend/`
  - React app (Vite + Tailwind). Components: `LiveKitClient.tsx`, `Transcript.tsx`, and simple UI.
- `packages/ingest/`
  - `ingest.py`: PDF → chunks → embeddings → LanceDB table
  - `requirements.txt`: ingest‑only deps
- `data/lancedb/`: LanceDB files (ignored by git)
- `scripts/`: Dev script (`dev_run_agent.sh`) and a RAG eval scaffold

### Setup

Prereqs:
- Python 3.11+
- Node 20+
- LiveKit Cloud project with `LIVEKIT_URL` (wss URL) + API key/secret
- OpenAI API key (for STT + LLM) and ElevenLabs key (for TTS)

1) Create and populate environment files

- Copy the example and fill in values:
  - `cp apps/agent/.env.example apps/agent/.env`

Key variables (see the example file):
- LIVEKIT_URL (must be `wss://<subdomain>.livekit.cloud`)
- LIVEKIT_API_KEY, LIVEKIT_API_SECRET
- OPENAI_API_KEY, MODEL_NAME (e.g. `gpt-4o-mini`)
- ELEVEN_API_KEY, ELEVEN_VOICE_ID, ELEVEN_TTS_MODEL
- NUTRITIONIX_APP_ID, NUTRITIONIX_API_KEY
- DB_DIR (`./data/lancedb`), TABLE (`israetel_pdf`), EMBED_MODEL (`BAAI/bge-small-en-v1.5`)

2) Install dependencies

```bash
# Backend agent
python -m venv .venv && source .venv/bin/activate
pip install -r apps/agent/requirements.txt

# Ingestion tool (separate set)
pip install -r packages/ingest/requirements.txt

# Frontend
cd apps/frontend && npm install
```

3) Build the LanceDB RAG store

```bash
# Ensure the PDF path is correct; default is packages/ingest/Scientific_Principles.pdf
PDF_PATH=packages/ingest/Scientific_Principles.pdf \
DB_DIR=./data/lancedb \
TABLE=israetel_pdf \
python packages/ingest/ingest.py --force
```

4) Run services locally

In three terminals:

```bash
# A) Agent service (joins rooms on demand)
./scripts/dev_run_agent.sh

# B) Token server (mints browser tokens and starts agent)
uvicorn apps.agent.token_server:app --reload --port 8000

# C) Frontend
cd apps/frontend && npm run dev
```

Then open the printed Vite URL (default `http://localhost:5173`). Click “Start Call”. The token server creates a new room and notifies the agent service to join. Speak; the transcript updates as both of you talk. Ask, for example, “How should I progress volume for quads?” to trigger RAG, or “Macros for a Chipotle bowl with chicken” to trigger Nutritionix.

### How RAG works

- Ingestion extracts text from the PDF, strips headers/footers, joins hyphenated words, normalizes whitespace, and chunks by ~900 words with 150 overlap.
- It embeds chunks with `BAAI/bge-small-en-v1.5` normalized vectors and writes to LanceDB, building a cosine index.
- On each user turn, the agent searches LanceDB (vector search), dedupes, and selects a diverse top‑k using MMR (lambda 0.65 default). It injects 2–3 compact snippets plus a strict answer contract into the LLM system messages. The agent prefers citing “chapter X page Y”.
- The frontend never sees the full RAG context; it only sees the final spoken transcript.

Key knobs (env): `RAG_K`, `RAG_MIN_SCORE`, `RAG_LAMBDA`, `RAG_DEBUG`.

### Tooling: Nutritionix

- When the LLM decides to call the tool, we hit Nutritionix’s natural language endpoint, normalize per‑item macros, and return a short speech‑friendly summary, plus a total if multiple items were detected.
- Progress/results are streamed to the UI via LiveKit data packets on topic `tool.events` to show inline status like “🔧 Nutrition analysis…” and results.

### Design decisions and assumptions

- RAG store: LanceDB chosen for zero‑ops local dev and solid cosine ANN. Embeddings via `bge-small-en` for a good speed/quality trade‑off on CPU.
- Retrieval: MMR re‑rank on cosine to avoid near‑duplicate chunks. A small candidate pool (8×k) balances latency and quality.
- Voice UX: Short answers optimized for TTS. We keep replies to 1–3 sentences unless explicitly asked for lists, where we extract verbatim enumerations.
- Costs: With `gpt-4o-mini` and short replies, OpenAI costs stay small. ElevenLabs TTS cost depends on seconds synthesized; brief replies plus short greetings keep this under the $10 budget with modest usage. LiveKit Cloud has a free tier suitable for testing.
- Hosting: Designed for local dev; you can deploy the two FastAPI apps (token + agent service) to a single small VM or ECS task. Both must share the same `LIVEKIT_URL` and API keys. The agent can also be run via `apps/agent/main.py` using the LiveKit Agents worker if preferred.

Limitations / trade‑offs:
- TTS uses ElevenLabs (cloud). If you need fully local TTS for cost or privacy, swap it for Piper/Coqui in the agent session config.
- Only one PDF corpus is indexed. Extending to multiple sources requires a small metadata tweak and optional per‑source routing.
- No authentication on the token server beyond the LiveKit token grant; for production, add auth and rate limits.

### Environment variables reference

See `apps/agent/.env.example` for the full list. Important ones:
- LIVEKIT_URL (wss), LIVEKIT_API_KEY, LIVEKIT_API_SECRET
- OPENAI_API_KEY, MODEL_NAME, LLM_TEMPERATURE
- ELEVEN_API_KEY, ELEVEN_VOICE_ID, ELEVEN_TTS_MODEL
- NUTRITIONIX_APP_ID, NUTRITIONIX_API_KEY
- DB_DIR, TABLE, EMBED_MODEL
- RAG_K, RAG_MIN_SCORE, RAG_LAMBDA, RAG_DEBUG
- AGENT_IDENTITY, AGENT_SERVICE_URL, CORS_ORIGIN

### Improvements
- tweak RAG parameters, methods
- try making chunks smaller
- track latency time for endpointing, stt, rag, llm, tts, playback