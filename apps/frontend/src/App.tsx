import React, { useMemo, useState } from "react";
import { LiveKitClient } from "./LiveKitClient";
import { Button } from "./components/ui/button";
import { Card, CardContent } from "./components/ui/card";
import { Avatar } from "./components/ui/avatar";
import mike from "./assets/mike.jpeg";
import { startCall } from "./api";

type Session = { room: string; identity: string; token: string; url: string };

export default function App() {
  const [started, setStarted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [speaking, setSpeaking] = useState(false);

  // Keep dev identity for other flows; /api/start will provide identity for the session
  const devIdentity = useMemo(() => {
    try {
      const key = "dm_identity";
      const existing = sessionStorage.getItem(key);
      if (existing) return existing;
      const next = `user-${Math.random().toString(36).slice(2, 8)}`;
      sessionStorage.setItem(key, next);
      return next;
    } catch {
      return `user-${Math.random().toString(36).slice(2, 8)}`;
    }
  }, []);

  const onStart = async () => {
    setLoading(true);
    try {
      const s = await startCall();
      setSession(s);      // { room, identity, token, url }
      setStarted(true);
    } catch (e) {
      console.error(e);
      alert("Failed to start call. Ensure the backend /api/start is running.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-background to-muted/20 p-6 dark:bg-[radial-gradient(ellipse_at_top,theme(colors.muted.DEFAULT)/0.25,transparent_60%)]">
      <div className="flex w-full max-w-lg flex-col items-center gap-6">
        {/* Removed outer card border by rendering content directly with subtle max-width; keep inner transcript box */}
        <div className={`flex w-full flex-col items-center ${!started ? "gap-7 p-2" : "gap-4 p-2"}`}>
          {/* Shared avatar with smooth size transition and speaking ring */}
          <div className={`${speaking ? "speaking-rings" : ""}`}>
            <Avatar
              src={mike}
              alt="Mike"
              className={`speaking-scale ${speaking ? "is-speaking" : ""} transition-[width,height] duration-500 ease-out ${!started ? "h-36 w-36" : "h-28 w-28"} shadow-inner ring-1 ring-border`}
            />
          </div>

          {!started ? (
            <>
              <div className="text-center">
                <h1 className="text-3xl font-semibold tracking-tight">Digital Mike</h1>
                <p className="text-sm text-muted-foreground">Real-time conversation with live transcript</p>
              </div>
              <Button size="lg" onClick={onStart} disabled={loading}>
                {loading ? "Starting..." : "Start Call"}
              </Button>
            </>
          ) : session ? (
            <div className="w-full">
              <LiveKitClient
                roomName={session.room}
                identity={session.identity || devIdentity}
                token={session.token}
                url={session.url}
                onEnd={() => { setStarted(false); setSession(null); }}
                onSpeakingChange={setSpeaking}
              />
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
