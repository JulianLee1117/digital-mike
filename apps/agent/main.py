import os
import json
import uuid
from typing import Optional
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (
    AgentSession, Agent, RoomInputOptions, RoomOutputOptions,
    ChatContext, ChatMessage, function_tool, RunContext, get_job_context, ToolError
)
from livekit import rtc
from livekit.plugins import openai as lk_openai
from livekit.plugins import elevenlabs as lk_elevenlabs
from livekit.plugins.silero import VAD
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from .persona import SYSTEM_PROMPT
from .rag.store import RAGStore
from .tools.nutritionix import lookup_macros, summarize_for_speech

import re
from .utils.logging import get_logger

logger = get_logger(__name__)

load_dotenv()  # loads apps/agent/.env if you `cd` here or export PWD

# ---------- Agent definition ----------
class DigitalMike(Agent):
    def __init__(self, room: Optional[rtc.Room] = None) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self.room = room
        # Lazily initialize RAG store on first use to avoid per-turn construction cost
        self._rag_store: RAGStore | None = None

    async def _emit_tool_event(self, kind: str, payload: dict) -> None:
        """
        Try RPC to the first non-agent participant; fall back to text stream topic 'tool.events'.
        """
        room = self.room
        if room is None:
            try:
                room = get_job_context().room  # available when launched via agents.cli
            except Exception:
                room = None

        if not room:
            return

        # For UI notifications a one-way data packet is simpler and more robust than RPC
        evt = {"type": kind, "payload": payload}
        data = json.dumps(evt)
        try:
            await room.local_participant.publish_data(
                data.encode("utf-8"), reliable=True, topic="tool.events"
            )
            print("[agent] published tool.events")
        except Exception as e:
            print("[agent] tool.events publish failed:", e)

    # Nutrition tool the LLM can call
    @function_tool(
        name="lookup_macros",
        description=(
            "Look up nutrition macros for a natural-language food description "
            "and return a short spoken summary. Use when the user asks about "
            "calories, protein, carbs, or fats."
        ),
    )
    async def tool_lookup_macros(self, context: RunContext, query: str) -> str:
        event_id = str(uuid.uuid4())
        await self._emit_tool_event(
            "nutritionix:start", {"id": event_id, "query": (query or "").strip()[:200]}
        )
        try:
            items = lookup_macros(query)
            preview = items[:3]
            await self._emit_tool_event(
                "nutritionix:result", {"id": event_id, "items": preview}
            )
            return summarize_for_speech(items)
        except Exception as e:
            await self._emit_tool_event(
                "nutritionix:error", {"id": event_id, "message": str(e)[:200]}
            )
            raise ToolError(f"Nutrition lookup failed: {e}")

    # RAG: inject context after the user finishes a turn, before LLM response
    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        try:
            text = _msg_text(new_message)
            if not text:
                return
            # Ignore very short interjections unless it's a question
            tokens = text.strip().split()
            if len(tokens) < 2 and "?" not in text:
                return
            # Brief voice-first policy
            turn_ctx.add_message(
                role="system",
                content=(
                    "Voice-first: Keep replies tight for speech. Prefer 1–3 short sentences;"
                    " add a brief plan only if useful."
                ),
            )
            rag_min_score = float(os.getenv("RAG_MIN_SCORE", "0.38"))
            rag_k = int(os.getenv("RAG_K", "4"))
            rag_lambda = float(os.getenv("RAG_LAMBDA", "0.65"))
            rag_debug = os.getenv("RAG_DEBUG", "0") == "1"

            # heuristics
            kw = ("how","what","why","should","sets","reps","rir","deload","volume","hypertrophy",
                  "strength","program","plan","workout","sra","mrv","mev","mav","fatigue","overload",
                  "variation","phase","protein","calories","macros","cut","bulk","diet")
            low = text.lower()
            is_training_query = ("?" in text) or any(k in low for k in kw)

            def sanitize_query(s: str) -> str:
                parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", s) if p.strip()]
                keep = [p for p in parts if ("?" in p) or any(k in p.lower() for k in kw)]
                out = " ".join(keep)[:512].strip()
                return out or s[:512].strip()

            turn_id = str(uuid.uuid4())

            if not is_training_query:
                if rag_debug:
                    logger.info("rag.decision", extra={"extra": {
                        "turn_id": turn_id, "decision": "skip", "reason": "non_training", "text": text[:160]
                    }})
                return

            query = sanitize_query(text)
            # Lazy-init shared RAG store once per agent instance
            if self._rag_store is None:
                self._rag_store = RAGStore()
            results = self._rag_store.search(query, k=rag_k, lambda_mult=rag_lambda, min_score=rag_min_score)

            if rag_debug:
                if results:
                    top = results[0]
                    logger.info("rag.candidates", extra={
                        "extra": {
                            "query": query[:200],
                            "k": rag_k, "returned": len(results), "lambda_mult": rag_lambda, "min_score": rag_min_score,
                            "top_score": round(top.get("score", -1.0), 3),
                            "top_cosine": round(top.get("cosine", -1.0), 3),
                            "top": [
                                {"page": r["page"], "chapter": r["chapter"], "score": round(r.get("score", -1.0), 3)}
                                for r in results[:5]
                            ],
                        }
                    })
                else:
                    logger.info("rag.candidates", extra={
                        "extra": {
                            "query": query[:200],
                            "k": rag_k, "returned": 0, "lambda_mult": rag_lambda, "min_score": rag_min_score,
                        }
                    })

            if not results:
                logger.info("rag.decision", extra={"extra": {
                    "turn_id": turn_id, "decision": "skip", "reason": "weak_retrieval", "min_score": rag_min_score
                }})
                return

            # Build ultra-compact RAG context and pick ONE best citation (chapter + page)
            lines = []
            top_cite = None
            collected_full_texts = []
            for i, r in enumerate(results[:3]):
                pg = r.get("page")
                ch = r.get("chapter")
                full_text = (r.get("text") or "").strip()
                snippet = full_text
                if not snippet:
                    continue
                if len(snippet) > 380:
                    snippet = snippet[:380].rstrip() + " …"
                # Normalize chapter label to avoid "Ch. Chapter"
                chapter_num = None
                if isinstance(ch, (str, int)):
                    try:
                        # Try to extract first integer in the string
                        chapter_num = int(str(ch).split()[0].strip().strip(':').strip('#'))
                    except Exception:
                        chapter_num = None
                # TTS-friendly citation phrase — always prefer chapter + page when present
                if chapter_num is not None:
                    cite = f"chapter {chapter_num} page {pg} in my book"
                else:
                    # try to keep any non-numeric chapter label if provided
                    if isinstance(ch, str) and ch.strip():
                        cite = f"{ch.strip()} page {pg} in my book"
                    else:
                        cite = f"page {pg} in my book"
                if top_cite is None:
                    top_cite = cite
                lines.append(f"({cite}) {snippet}")
                # keep the untruncated text for downstream extraction
                collected_full_texts.append(full_text)

            rag_block = (
                "RAG CONTEXT — 'Scientific Principles of Strength Training' snippets:\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n\nGround answers in these."
            )
            # Make RAG visible as system guidance
            turn_ctx.add_message(role="system", content=rag_block)

            # Enforce the answer contract and citation style (explicitly mention chapter + page)
            rag_strict = os.getenv("RAG_STRICT", "1") == "1"
            contract = (
                "Answer contract: Acknowledge briefly, then max 2 sentences in conversational coach voice."
                " Embed any plan naturally and briefly."
                " If helpful, include at most one inline cite as: 'based on {"
                + (top_cite or "page ? in my book") + "}'."
                " Always prefer 'chapter X page Y' if available; say the words 'chapter' and 'page', no abbreviations."
                + (" Only use the RAG CONTEXT for factual claims; don't introduce facts not present."
                   " If the RAG CONTEXT seems insufficient, say so briefly or ask a clarifying question."
                   if rag_strict else "")
                + " When enumerating lists that appear in the RAG CONTEXT, reproduce the item labels verbatim and in order before any paraphrase."
                " In medical/health contexts (e.g., heart conditions), skip citations and keep advice conservative."
            )
            turn_ctx.add_message(role="system", content=contract)

            # Generic enumeration extraction from retrieved texts; supports numbered and bulleted lists
            def _extract_enumerated_lines(texts: list[str], max_items: int = 10) -> list[str]:
                import re as _re
                items: list[str] = []
                # Pass 1: line-based capture (works if ingestion preserved newlines)
                line_patterns = [
                    r"^\s*(?:\(?\d{1,2}\)?[\.)]|\d{1,2}\.)\s+(.+?)\s*$",  # 1.) or (1) or 1.
                    r"^\s*[-•]\s+(.+?)\s*$",  # bullet lines
                    r"^\s*\(?[a-zA-Z]\)?[\.)]\s+(.+?)\s*$",  # a.) or a) or (a)
                ]
                compiled_lines = [_re.compile(p) for p in line_patterns]
                for t in texts:
                    for line in (t or "").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        for cp in compiled_lines:
                            m = cp.match(line)
                            if m:
                                content = m.group(1).strip()
                                content = _re.sub(r"\s+[\-–—]$", "", content)
                                items.append(content)
                                break
                        if len(items) >= max_items:
                            break
                    if len(items) >= max_items:
                        break
                if items:
                    return items[:max_items]

                # Pass 2: inline capture for flattened text (no newlines)
                joined = " \n ".join(texts)
                # Digit enumerations like: 1.) text 2.) text ...
                enum_digits = _re.compile(
                    r"(?:^|\s)\(?(?P<num>\d{1,2})\)?[\.)]\s+(?P<item>.+?)(?=(?:\s\(?\d{1,2}\)?[\.)]\s+)|$)",
                    flags=_re.DOTALL,
                )
                # Letter enumerations like: a.) text b.) text ... (case-insensitive)
                enum_letters = _re.compile(
                    r"(?i)(?:^|\s)\(?(?P<let>[a-z])\)?[\.)]\s+(?P<item>.+?)(?=(?:\s\(?[a-z]\)?[\.)]\s+)|$)",
                    flags=_re.DOTALL,
                )
                # Bullet enumerations like: • item • item ...
                bullet_re = _re.compile(r"•\s+(?P<item>[^•]+)(?=•|$)")

                enum_hits = []
                for m in enum_digits.finditer(joined):
                    try:
                        n = int(m.group("num"))
                    except ValueError:
                        n = -1
                    if 1 <= n <= 20:
                        enum_hits.append((n, m.group("item").strip()))
                if enum_hits:
                    enum_hits.sort(key=lambda x: x[0])
                    for _, it in enum_hits:
                        clean = _re.sub(r"\s+\-+$", "", it).strip()
                        if clean:
                            items.append(clean)
                else:
                    # Try lettered sequences a.) b.) c.) ... up to 26
                    let_hits = []
                    for m in enum_letters.finditer(joined):
                        let = (m.group("let") or "").lower()
                        if let and 'a' <= let <= 'z':
                            idx = ord(let) - ord('a') + 1
                            let_hits.append((idx, m.group("item").strip()))
                    if let_hits:
                        let_hits.sort(key=lambda x: x[0])
                        for _, it in let_hits:
                            clean = _re.sub(r"\s+\-+$", "", it).strip()
                            if clean:
                                items.append(clean)
                    else:
                        for m in bullet_re.finditer(joined):
                            it = m.group("item").strip()
                            clean = _re.sub(r"\s+\-+$", "", it).strip()
                            if clean:
                                items.append(clean)

                return items[:max_items]

            enumerated = _extract_enumerated_lines(collected_full_texts, max_items=10)
            if enumerated:
                # Keep it compact and verbatim-first
                verbatim_block = (
                    "FACTS (verbatim; when enumerating, list these exact lines first):\n" +
                    "\n".join(f"- {it}" for it in enumerated[:6])
                )
                turn_ctx.add_message(role="system", content=verbatim_block)
                # If the user is clearly asking for a list/enumeration, force verbatim listing first
                ask_enum = any(w in low for w in ["list", "what are", "which are", "four", "4", "items", "variables", "layers"])
                if ask_enum:
                    # Build a response template with exact items, and forbid markdown emphasis
                    template_list = "\n".join(f"- {it}" for it in enumerated[:6])
                    turn_ctx.add_message(
                        role="system",
                        content=(
                            "Output requirements: Do not use bold/italics/asterisks/markdown. "
                            "Start your answer with these exact items verbatim and in order, one per line, prefixed by '- ':\n"
                            + template_list +
                            "\nAfter the list, add exactly one short clarification sentence. "
                            "Do not introduce new items, do not rename items, and keep tone professional."
                        ),
                    )
                if rag_debug:
                    logger.info("rag.enumerated", extra={"extra": {"turn_id": turn_id, "ask_enum": ask_enum, "items": enumerated[:6]}})

            if rag_debug:
                logger.info("rag.injected", extra={"extra": {
                    "turn_id": turn_id, "cite": (top_cite or ""), "rag_block_preview": rag_block[:220]
                }})
        except Exception:
            # never let the hook crash the session
            pass

async def entrypoint(ctx: agents.JobContext):
    # Build STT/LLM/TTS/VAD/turn-detection pipeline using LiveKit plugins
    session = AgentSession(
        stt=lk_openai.STT(),  # OpenAI Whisper/4o transcription
        llm=lk_openai.LLM(model=os.getenv("MODEL_NAME", "gpt-4o-mini"), temperature=float(os.getenv("LLM_TEMPERATURE", "0.2"))),
        tts=lk_elevenlabs.TTS(
            voice_id=os.getenv("ELEVEN_VOICE_ID", "eGCULX6fOY83AsIVPZ8O"),
            model=os.getenv("ELEVEN_TTS_MODEL", "eleven_multilingual_v2"),
        ),
        vad=VAD.load(min_speech_duration=0.25, min_silence_duration=0.4),
        turn_detection=MultilingualModel(),
        use_tts_aligned_transcript=True,
    )

    await session.start(
        room=ctx.room,
        agent=DigitalMike(),
        room_input_options=RoomInputOptions(
            audio_enabled=True,
            text_enabled=True,  # optional: allows chat messages from client
        ),
        room_output_options=RoomOutputOptions(
            audio_enabled=True,
            transcription_enabled=True,  # publishes transcripts to clients
            sync_transcription=False,    # stream transcripts ASAP
        ),
    )

    # Optional: greet
    await session.generate_reply(instructions="Greet the user in one sentence and ask their training goal.")

if __name__ == "__main__":
    # Provides `console`, `dev`, `start`, `download-files` commands
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))

def _msg_text(m: ChatMessage) -> str:
    tc = getattr(m, "text_content", None)
    if callable(tc):
        return (tc() or "").strip()
    if isinstance(tc, str):
        return tc.strip()
    c = getattr(m, "content", None)
    return (c or "").strip() if isinstance(c, str) else ""
