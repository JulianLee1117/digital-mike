import os
from dotenv import load_dotenv

from livekit import agents
from livekit.agents import (
    AgentSession, Agent, RoomInputOptions, RoomOutputOptions,
    ChatContext, ChatMessage, function_tool, RunContext
)
from livekit.plugins import openai as lk_openai
from livekit.plugins.silero import VAD
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from persona import SYSTEM_PROMPT
from rag.store import RAGStore
from tools.nutritionix import lookup_macros, summarize_for_speech

load_dotenv()  # loads apps/agent/.env if you `cd` here or export PWD

# ---------- Agent definition ----------
class DigitalMike(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

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
        items = lookup_macros(query)
        return summarize_for_speech(items)

    # RAG: inject context after the user finishes a turn, before LLM response
    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        try:
            text = _msg_text(new_message)
            if not text:
                return
            # optional brevity policy for TTS
            turn_ctx.add_message(role="system", content="Keep replies to 1–3 short sentences for voice output.")
            rag = RAGStore()
            results = rag.search(text, k=4)
            if not results:
                return
            lines = []
            for r in results[:4]:
                pg = r.get("page")
                snippet = (r.get("text") or "").strip()
                if not snippet:
                    continue
                if len(snippet) > 380:
                    snippet = snippet[:380].rstrip() + " …"
                lines.append(f"(p.{pg}) {snippet}")
            rag_block = (
                "RAG CONTEXT — excerpts from 'Scientific Principles of Strength Training':\n"
                + "\n".join(f"- {ln}" for ln in lines)
                + "\n\nWhen citing, include page numbers like (p.X)."
            )
            turn_ctx.add_message(role="assistant", content=rag_block)
        except Exception:
            # never let the hook crash the session
            pass

async def entrypoint(ctx: agents.JobContext):
    # Build STT/LLM/TTS/VAD/turn-detection pipeline using LiveKit plugins
    session = AgentSession(
        stt=lk_openai.STT(),  # OpenAI Whisper/4o transcription
        llm=lk_openai.LLM(model=os.getenv("MODEL_NAME", "gpt-4o-mini")),
        tts=lk_openai.TTS(
            model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
            voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
        ),
        vad=VAD.load(),
        turn_detection=MultilingualModel(),
        use_tts_aligned_transcript=False,
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
