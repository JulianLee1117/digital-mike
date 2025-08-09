import os, sys, asyncio
from dotenv import load_dotenv
from livekit import rtc, api
from livekit.agents import AgentSession, RoomInputOptions, RoomOutputOptions
from livekit.plugins import openai as lk_openai
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

    async def start_session():
        nonlocal session
        if session is not None:
            return
        session = AgentSession(
            stt=lk_openai.STT(),
            llm=lk_openai.LLM(model=os.getenv("MODEL_NAME", "gpt-4o-mini")),
            tts=lk_openai.TTS(
                model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
            ),
            vad=VAD.load(min_speech_duration=0.1, min_silence_duration=0.4),
        )
        await session.start(
            room=room,
            agent=DigitalMike(),
            room_input_options=RoomInputOptions(audio_enabled=True, text_enabled=True),
            room_output_options=RoomOutputOptions(audio_enabled=True, transcription_enabled=True, sync_transcription=False),
        )
        print("[agent] session started")
        await session.say("Audio check. You should hear me now.", allow_interruptions=False)
        await session.generate_reply(instructions="Greet the user in one sentence and ask their training goal.")

    async def stop_session():
        nonlocal session
        if session is None:
            return
        try:
            await session.aclose()
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
        asyncio.create_task(start_session())

    @room.on("participant_disconnected")
    def _on_user_leave(p, reason=None):
        if p.identity == agent_identity:
            return
        print("[agent] user left:", p.identity, "reason=", reason)
        # if nobody else remains, stop the session
        if non_agent_participants() == 0:
            asyncio.create_task(stop_session())

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
