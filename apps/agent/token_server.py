import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from livekit import api
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()  # ensure LIVEKIT_* are available when running uvicorn

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

@app.get("/api/token")
def get_token(room: str = Query(...), identity: str = Query(...)):
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(status_code=500, detail="Missing LIVEKIT_URL/API_KEY/API_SECRET")
    try:
        grant = api.VideoGrants(room=room, room_join=True, can_publish=True, can_subscribe=True)
        at = api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
        at.with_identity(identity).with_name(identity).with_grants(grant).with_ttl(timedelta(hours=1))
        return {"url": LIVEKIT_URL, "token": at.to_jwt()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"token generation error: {e}")
