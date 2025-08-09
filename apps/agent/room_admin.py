import sys, asyncio
from dotenv import load_dotenv
from livekit.api import (
  LiveKitAPI,
  ListRoomsRequest, ListParticipantsRequest,
  RoomParticipantIdentity, DeleteRoomRequest,
)

load_dotenv()

async def list_rooms():
  async with LiveKitAPI() as lkapi:
    res = await lkapi.room.list_rooms(ListRoomsRequest())
    for r in res.rooms:
      print(r.name, r.num_participants)

async def list_parts(room: str):
  async with LiveKitAPI() as lkapi:
    res = await lkapi.room.list_participants(ListParticipantsRequest(room=room))
    for p in res.participants:
      print(p.identity, p.kind.name, "tracks:", len(p.tracks))

async def rmall(room: str):
  async with LiveKitAPI() as lkapi:
    res = await lkapi.room.list_participants(ListParticipantsRequest(room=room))
    for p in res.participants:
      await lkapi.room.remove_participant(RoomParticipantIdentity(room=room, identity=p.identity))
      print("removed", p.identity)

async def delete_room(room: str):
  async with LiveKitAPI() as lkapi:
    await lkapi.room.delete_room(DeleteRoomRequest(room=room))
    print("deleted", room)

async def main():
  if len(sys.argv) < 2:
    print("usage: room_admin.py [list|parts|rmall|del] <room?>")
    return
  cmd = sys.argv[1]
  if cmd == "list":
    await list_rooms()
  elif cmd == "parts":
    await list_parts(sys.argv[2])
  elif cmd == "rmall":
    await rmall(sys.argv[2])
  elif cmd == "del":
    await delete_room(sys.argv[2])

if __name__ == "__main__":
  asyncio.run(main())
