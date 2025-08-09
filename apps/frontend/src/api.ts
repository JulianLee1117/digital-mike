export async function getToken(room: string, identity: string) {
  const url = `http://localhost:8000/api/token?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(identity)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Token fetch failed: ${res.status} ${await res.text()}`);
  return res.json() as Promise<{ token: string; url: string }>;
}

// NEW: mint a fresh room + identity + token for a new call
export async function startCall() {
  const res = await fetch("http://localhost:8000/api/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!res.ok) throw new Error(`Start failed: ${res.status} ${await res.text()}`);
  // { url, room, identity, token }
  return res.json() as Promise<{ url: string; room: string; identity: string; token: string }>;
}
