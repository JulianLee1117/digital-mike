export async function getToken(room: string, identity: string) {
  const url = `http://localhost:8000/api/token?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(identity)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Token fetch failed: ${res.status} ${await res.text()}`);
  return res.json() as Promise<{ token: string; url: string }>;
}
