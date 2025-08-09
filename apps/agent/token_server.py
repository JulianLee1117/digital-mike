import os, secrets
from datetime import timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from livekit import api
import httpx

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
AGENT_SERVICE_URL = os.getenv("AGENT_SERVICE_URL", "http://localhost:9001")

class StartResp(BaseModel):
    url: str
    room: str
    identity: str
    token: str

@app.post("/api/start", response_model=StartResp)
async def start_call():
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(status_code=500, detail="Missing LIVEKIT_URL/API_KEY/API_SECRET")

    room = f"digital-mike-{secrets.token_hex(3)}"
    identity = f"user-{secrets.token_hex(3)}"

    # Best practice: proactively create the room with an EmptyTimeout using RoomService
    try:
        async with api.LiveKitAPI() as lkapi:
            await lkapi.room.create_room(api.CreateRoomRequest(
                name=room,
                empty_timeout=15 * 60,  # 15 minutes auto-cleanup
                max_participants=4,
            ))
    except Exception:
        # If it already exists or create failed, we still attempt to mint a token
        pass

    grant = api.VideoGrants(room=room, room_join=True, can_publish=True, can_subscribe=True)
    at = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    at.with_identity(identity).with_name(identity).with_grants(grant).with_ttl(timedelta(hours=1))
    token = at.to_jwt()

    # notify agent service to join this room
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{AGENT_SERVICE_URL}/join", json={"room": room})
    except Exception as e:
        # log and continue; user can still join, agent may join slightly later
        print("[token] failed to notify agent:", e)

    return StartResp(url=LIVEKIT_URL, room=room, identity=identity, token=token)


# Backcompat: allow minting a token for an existing room/identity when not using /api/start
class TokenResp(BaseModel):
    token: str
    url: str


@app.get("/api/token", response_model=TokenResp)
async def token(room: str, identity: str):
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(status_code=500, detail="Missing LIVEKIT_URL/API_KEY/API_SECRET")
    grant = api.VideoGrants(room=room, room_join=True, can_publish=True, can_subscribe=True)
    at = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
    at.with_identity(identity).with_name(identity).with_grants(grant).with_ttl(timedelta(hours=1))
    token = at.to_jwt()
    return TokenResp(token=token, url=LIVEKIT_URL)
