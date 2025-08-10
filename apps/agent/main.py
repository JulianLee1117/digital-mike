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

load_dotenv()  # loads apps/agent/.env if you `cd` here or export PWD

# ---------- Agent definition ----------
class DigitalMike(Agent):
    def __init__(self, room: Optional[rtc.Room] = None) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self.room = room

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
            rag = RAGStore()
            results = rag.search(text, k=4)
            if not results:
                # Weak retrieval fallback: instruct model to stay generic and honest
                turn_ctx.add_message(
                    role="system",
                    content=(
                        "Retrieval is weak. Provide a generic, evidence-based answer."
                        " Say you’re not fully certain. Don’t fabricate citations."
                        " Max 2 short sentences in conversational coach voice."
                        " If the user asks for plain language, define acronyms briefly"
                        " and avoid jargon."
                    ),
                )
                return

            # Build ultra-compact RAG context and pick ONE best citation (chapter + page)
            lines = []
            top_cite = None
            for i, r in enumerate(results[:3]):
                pg = r.get("page")
                ch = r.get("chapter")
                snippet = (r.get("text") or "").strip()
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

            rag_block = (
                "RAG CONTEXT — 'Scientific Principles of Strength Training' snippets:\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n\nGround answers in these."
            )
            # Make RAG visible as system guidance
            turn_ctx.add_message(role="system", content=rag_block)

            # Enforce the answer contract and citation style (explicitly mention chapter + page)
            contract = (
                "Answer contract: Acknowledge briefly, then max 2 sentences in conversational coach voice."
                " Embed any plan naturally and briefly."
                " If helpful, include at most one inline cite as: 'based on {"
                + (top_cite or "page ? in my book") + "}'."
                " Always prefer 'chapter X page Y' if available; say the words 'chapter' and 'page', no abbreviations."
                " In medical/health contexts (e.g., heart conditions), skip citations and keep advice conservative."
            )
            turn_ctx.add_message(role="system", content=contract)
        except Exception:
            # never let the hook crash the session
            pass

async def entrypoint(ctx: agents.JobContext):
    # Build STT/LLM/TTS/VAD/turn-detection pipeline using LiveKit plugins
    session = AgentSession(
        stt=lk_openai.STT(),  # OpenAI Whisper/4o transcription
        llm=lk_openai.LLM(model=os.getenv("MODEL_NAME", "gpt-4o-mini")),
        tts=lk_elevenlabs.TTS(
            voice_id=os.getenv("ELEVEN_VOICE_ID", "eGCULX6fOY83AsIVPZ8O"),
            model=os.getenv("ELEVEN_TTS_MODEL", "eleven_multilingual_v2"),
        ),
        # More conservative VAD to avoid mid-thought interruptions
        vad=VAD.load(min_speech_duration=0.25, min_silence_duration=0.8),
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
