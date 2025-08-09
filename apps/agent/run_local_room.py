import os, sys, asyncio
import aiohttp
from dotenv import load_dotenv
from livekit import rtc, api
from livekit.agents import AgentSession, RoomInputOptions, RoomOutputOptions
from livekit.plugins import openai as lk_openai
from livekit.plugins import elevenlabs as lk_elevenlabs
from livekit.plugins.silero import VAD
from main import DigitalMike

load_dotenv()

async def main():
    room_name = os.getenv("ROOM_NAME", "digital-mike")
    agent_identity = os.getenv("AGENT_IDENTITY", "digital-mike-agent")

    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "OPENAI_API_KEY"):
        if not os.getenv(k):
            print(f"[agent] MISSING ENV: {k}", file=sys.stderr)

    print(f"[agent] minting token for identity={agent_identity} room={room_name}")
    at = api.AccessToken(api_key=os.environ["LIVEKIT_API_KEY"], api_secret=os.environ["LIVEKIT_API_SECRET"])
    at.with_identity(agent_identity).with_grants(api.VideoGrants(room=room_name, room_join=True, can_publish=True, can_subscribe=True))
    token = at.to_jwt()

    room = rtc.Room()
    await room.connect(os.environ["LIVEKIT_URL"], token)
    print(f"[agent] connected as {room.local_participant.identity!r} in room {room.name!r}")

    session: AgentSession | None = None
    # when last user leaves, schedule a delayed stop; cancel if someone rejoins
    pending_stop: asyncio.Task | None = None

    async def start_session():
        nonlocal session
        if session is not None:
            return
        eleven_http = aiohttp.ClientSession()
        session = AgentSession(
            stt=lk_openai.STT(),
            llm=lk_openai.LLM(model=os.getenv("MODEL_NAME", "gpt-4o-mini")),
            tts=lk_elevenlabs.TTS(
                voice_id=os.getenv("ELEVEN_VOICE_ID", "eGCULX6fOY83AsIVPZ8O"),
                model=os.getenv("ELEVEN_TTS_MODEL", "eleven_multilingual_v2"),
                http_session=eleven_http,
            ),
            vad=VAD.load(min_speech_duration=0.25, min_silence_duration=0.8),
        )
        await session.start(
            room=room,
            agent=DigitalMike(),
            room_input_options=RoomInputOptions(audio_enabled=True, text_enabled=True),
            room_output_options=RoomOutputOptions(audio_enabled=True, transcription_enabled=True, sync_transcription=False),
        )
        print("[agent] session started")
        try:
            await session.generate_reply(
                instructions="Ask the user for their training goals in one short sentence."
            )
        except Exception as e:
            print("[agent] initial reply skipped:", e)

    async def stop_session():
        nonlocal session
        if session is None:
            return
        try:
            await session.aclose()
        except Exception:
            pass
        # Close custom ElevenLabs session if present
        try:
            tts = session._voice._tts if session else None
            http = getattr(tts, "_session", None) if tts else None
            if http and not http.closed:
                await http.close()
        except Exception:
            pass
        session = None
        print("[agent] session stopped")

    def non_agent_participants() -> int:
        return sum(1 for _, p in room.remote_participants.items() if p.identity != agent_identity)

    @room.on("participant_connected")
    def _on_user_join(p):
        if p.identity == agent_identity:
            return
        print("[agent] participant connected:", p.identity)
        # cancel any pending stop if a user returns quickly
        nonlocal pending_stop
        if pending_stop and not pending_stop.done():
            pending_stop.cancel()
            pending_stop = None
        asyncio.create_task(start_session())

    @room.on("participant_disconnected")
    def _on_user_leave(p, reason=None):
        if p.identity == agent_identity:
            return
        print("[agent] user left:", p.identity, "reason=", reason)
        # if nobody else remains, schedule a delayed stop so quick reconnects don't kill the session
        if non_agent_participants() == 0:
            nonlocal pending_stop
            async def delayed():
                try:
                    await asyncio.sleep(10)
                    if non_agent_participants() == 0:
                        await stop_session()
                except asyncio.CancelledError:
                    pass
            # cancel previous pending stop and schedule a new one
            if pending_stop and not pending_stop.done():
                pending_stop.cancel()
            pending_stop = asyncio.create_task(delayed())

    # if a user is already in the room, start once
    if non_agent_participants() > 0:
        await start_session()

    # keep running
    try:
        await asyncio.Event().wait()
    finally:
        await stop_session()
        try:
            await room.disconnect()
        except Exception:
            pass

if __name__ == "__main__":
    asyncio.run(main())
