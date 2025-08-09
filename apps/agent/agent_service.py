import asyncio
import os
import aiohttp
from datetime import timedelta

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dataclasses import dataclass, field
from livekit import api
from livekit import rtc

# IMPORTANT: load env first
load_dotenv()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("CORS_ORIGIN", "*")],
    allow_methods=["*"],
    allow_headers=["*"],
)

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

AGENT_IDENTITY = os.getenv("AGENT_IDENTITY", "digital-mike-agent")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")

# Per-room state (internal only). Use dataclass to avoid Pydantic schema for asyncio.Task
@dataclass
class RoomState:
    room_name: str
    connect_task: asyncio.Task | None = field(default=None, repr=False)


rooms: dict[str, RoomState] = {}


class JoinReq(BaseModel):
    room: str


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/join")
async def join(req: JoinReq):
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(status_code=500, detail="Missing LIVEKIT_URL/API_KEY/API_SECRET")

    room_name = req.room
    st = rooms.get(room_name)
    if st and st.connect_task and not st.connect_task.done():
        # already connecting/connected
        return {"status": "existing", "room": room_name}

    # start background connector
    task = asyncio.create_task(_connect_and_run_room(room_name))
    rooms[room_name] = RoomState(room_name=room_name, connect_task=task)
    return {"status": "started", "room": room_name}


async def _connect_and_run_room(room_name: str) -> None:
    print(f"[agent] starting background task for room={room_name}")
    try:
        from .main import DigitalMike  # late import to keep module path stable
        from livekit.agents import AgentSession, RoomInputOptions, RoomOutputOptions
        from livekit.plugins import openai as lk_openai
        from livekit.plugins import elevenlabs as lk_elevenlabs
        from livekit.plugins.silero import VAD

        # Mint agent token scoped to this room
        at = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        at.with_identity(AGENT_IDENTITY).with_name(AGENT_IDENTITY).with_grants(
            api.VideoGrants(room=room_name, room_join=True, can_publish=True, can_subscribe=True)
        ).with_ttl(timedelta(hours=1))
        token = at.to_jwt()

        room = rtc.Room()
        print(f"[agent] connecting to room={room_name} at {LIVEKIT_URL}")
        try:
            await room.connect(LIVEKIT_URL, token)
        except Exception as e:
            print("[agent] room.connect failed:", e)
            return
        print(f"[agent] connected as {room.local_participant.identity!r} in room {room.name!r}")

        session: AgentSession | None = None
        eleven_http: aiohttp.ClientSession | None = None
        pending_stop: asyncio.Task | None = None

        def non_agent_participants() -> int:
            return sum(1 for _, p in room.remote_participants.items() if p.identity != AGENT_IDENTITY)

        async def start_session() -> None:
            nonlocal session
            if session is not None:
                return
            if not os.getenv("OPENAI_API_KEY"):
                print("[agent] OPENAI_API_KEY missing; STT/LLM may fail.")
            if not os.getenv("ELEVEN_API_KEY"):
                print("[agent] ELEVEN_API_KEY missing; TTS may fail.")
            # Create a dedicated HTTP session for ElevenLabs when running outside a JobContext
            eleven_http = aiohttp.ClientSession()
            session = AgentSession(
                stt=lk_openai.STT(),
                llm=lk_openai.LLM(model=MODEL_NAME),
                tts=lk_elevenlabs.TTS(
                    voice_id=os.getenv("ELEVEN_VOICE_ID", "eGCULX6fOY83AsIVPZ8O"),
                    model=os.getenv("ELEVEN_TTS_MODEL", "eleven_multilingual_v2"),
                    http_session=eleven_http,
                ),
                # More conservative VAD to reduce interruptions between short pauses
                vad=VAD.load(min_speech_duration=0.25, min_silence_duration=0.8),
                use_tts_aligned_transcript=True,
            )
            try:
                await session.start(
                    room=room,
                    agent=DigitalMike(),
                    room_input_options=RoomInputOptions(audio_enabled=True, text_enabled=True),
                    room_output_options=RoomOutputOptions(
                        audio_enabled=True, transcription_enabled=True, sync_transcription=False
                    ),
                )
            except Exception as e:
                print("[agent] session.start failed:", e)
                try:
                    await session.aclose()
                except Exception:
                    pass
                try:
                    if eleven_http and not eleven_http.closed:
                        await eleven_http.close()
                except Exception:
                    pass
                session = None
                eleven_http = None
                return
            print("[agent] session started")
            # First turn: concise greeting that asks for training goals
            try:
                await session.generate_reply(
                    instructions="Ask the user for their training goals in one short sentence."
                )
            except Exception as e:
                print("[agent] initial reply skipped:", e)

        async def stop_session() -> None:
            nonlocal session
            if session is None:
                return
            try:
                await session.aclose()
            except Exception:
                pass
            session = None
            try:
                if eleven_http and not eleven_http.closed:
                    await eleven_http.close()
            except Exception:
                pass
            eleven_http = None
            print("[agent] session stopped")

        @room.on("participant_connected")
        def _on_user_join(p):
            if p.identity == AGENT_IDENTITY:
                return
            print("[agent] participant connected:", p.identity)
            nonlocal pending_stop
            if pending_stop and not pending_stop.done():
                pending_stop.cancel()
                pending_stop = None
            asyncio.create_task(start_session())

        @room.on("participant_disconnected")
        def _on_user_leave(p, reason=None):
            if p.identity == AGENT_IDENTITY:
                return
            print("[agent] user left:", p.identity, "reason=", reason)
            if non_agent_participants() == 0:
                nonlocal pending_stop
                async def delayed():
                    try:
                        await asyncio.sleep(10)
                        if non_agent_participants() == 0:
                            await stop_session()
                            try:
                                await room.disconnect()
                            except Exception:
                                pass
                    except asyncio.CancelledError:
                        pass
                if pending_stop and not pending_stop.done():
                    pending_stop.cancel()
                pending_stop = asyncio.create_task(delayed())

        # If a user is already present, start immediately
        if non_agent_participants() > 0:
            await start_session()

        # Keep process bound to this room until disconnect
        try:
            await asyncio.Event().wait()
        finally:
            await stop_session()
            try:
                await room.disconnect()
            except Exception:
                pass
            # cleanup registry
            try:
                st = rooms.get(room_name)
                if st and st.connect_task and not st.connect_task.done():
                    st.connect_task.cancel()
            except Exception:
                pass
            rooms.pop(room_name, None)
    except Exception as e:
        print("[agent] background task crashed:", e)


