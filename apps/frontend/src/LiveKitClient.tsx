import React, { useEffect, useRef, useState } from "react";
import {
  Room, RoomEvent, Track, RemoteAudioTrack,
  setLogLevel, LogLevel,
} from "livekit-client";
import { getToken } from "./api";
import { Transcript, TranscriptItem } from "./Transcript";
import { Button } from "./components/ui/button";

setLogLevel(LogLevel.info);

export function LiveKitClient({
  roomName,
  identity,
  onEnd,
  token,
  url,
  onSpeakingChange,
}: { roomName: string; identity: string; onEnd?: () => void; token?: string; url?: string; onSpeakingChange?: (speaking: boolean) => void }) {
  const [status, setStatus] = useState("disconnected");
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [needsUnlock, setNeedsUnlock] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const audioElsRef = useRef<HTMLAudioElement[]>([]);
  // Map trackSid → speaker ('user' | 'mike') for accurate attribution
  const trackToSpeakerRef = useRef<Record<string, "user" | "mike">>({});
  // Maintain one live partial state per speaker/track key with last update time for segmentation
  const partialStateRef = useRef<Record<string, { id: string; updatedAt: number }>>({});

  useEffect(() => {
    let isMounted = true;
    (async () => {
      setStatus("connecting");

      // NEW: prefer pre-minted token/url from /api/start
      const creds = token && url ? { token, url } : await getToken(roomName, identity);

      const room = new Room();
      roomRef.current = room;

      await room.connect(creds.url, creds.token, { autoSubscribe: true });
      if (!isMounted) return;
      setStatus("connected");

      // unlock audio (required on iOS/Safari)
      try { await room.startAudio(); } catch {}

      // publish mic
      await room.localParticipant.setMicrophoneEnabled(true);

      // playback unlock signal
      room.on(RoomEvent.AudioPlaybackStatusChanged, (canPlayback) => {
        if (!canPlayback) setNeedsUnlock(true);
      });

      // logs
      room.on(RoomEvent.ParticipantConnected, (p) => console.log("[fe] joined:", p.identity));
      room.on(RoomEvent.ParticipantDisconnected, (p) => console.log("[fe] left:", p.identity));
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const activeIds = speakers.map(s => s.identity);
        // naive heuristic: if any remote participant is speaking, show ring
        const remoteSpeaking = activeIds.some(id => id !== room.localParticipant.identity);
        try { onSpeakingChange?.(remoteSpeaking); } catch {}
        console.log("[fe] active speakers:", activeIds);
      });

      // attach audio on subscribe
      room.on(RoomEvent.TrackSubscribed, async (track, pub, participant) => {
        console.log("[fe] TrackSubscribed:", track.kind, "from", participant.identity);
        if (track.kind === Track.Kind.Audio) {
          // map remote agent audio track → 'mike'
          try { if ((pub as any)?.trackSid) { trackToSpeakerRef.current[(pub as any).trackSid] = "mike"; } } catch {}
          const el = (track as RemoteAudioTrack).attach() as HTMLAudioElement;
          el.style.display = "none";
          el.muted = false;
          el.volume = 1;
          el.setAttribute("playsinline", "true");
          document.body.appendChild(el);
          audioElsRef.current.push(el);
          try { await room.startAudio(); } catch {}
          try { await el.play(); } catch (e) { console.warn("[fe] el.play blocked", e); setNeedsUnlock(true); }
        }
      });

      // attach any audio already published by the agent
      for (const [, p] of room.remoteParticipants) {
        for (const [, pub] of p.audioTrackPublications) {
          if (!pub.isSubscribed) {
            try { await pub.setSubscribed(true); } catch (e) { console.warn("[fe] subscribe failed", e); }
          }
          const tr = pub.track;
          if (pub.isSubscribed && tr) {
            console.log("[fe] attaching pre-existing audio from", p.identity);
            try { if ((pub as any)?.trackSid) { trackToSpeakerRef.current[(pub as any).trackSid] = "mike"; } } catch {}
            const el = (tr as RemoteAudioTrack).attach() as HTMLAudioElement;
            el.style.display = "none";
            el.muted = false;
            el.volume = 1;
            el.setAttribute("playsinline", "true");
            document.body.appendChild(el);
            audioElsRef.current.push(el);
            try { await room.startAudio(); } catch {}
            try { await el.play(); } catch (e) { console.warn("[fe] el.play blocked", e); setNeedsUnlock(true); }
          }
        }
      }

      // map local mic publications → 'user'
      for (const [, pub] of room.localParticipant.audioTrackPublications) {
        try { if ((pub as any)?.trackSid) { trackToSpeakerRef.current[(pub as any).trackSid] = "user"; } } catch {}
      }

      // streaming transcriptions (lk.transcription)
      room.registerTextStreamHandler("lk.transcription", async (reader, pinfo) => {
        const attrs = (reader as any).info?.attributes as Record<string, string> | undefined;
        const from = pinfo.identity;
        const trk = attrs?.["lk.transcribed_track_id"];
        let speaker: "user" | "mike" | undefined = trk ? trackToSpeakerRef.current[trk] : undefined;
        if (!speaker) {
          const isUser = from === room.localParticipant.identity;
          speaker = isUser ? "user" : "mike";
        }

        const text = await reader.readAll();
        const isFinal = attrs?.["lk.transcription_final"] === "true";

        const key = trk ? `${speaker}:${trk}` : `${speaker}`;
        const now = Date.now();
        const existing = partialStateRef.current[key];

        if (!isFinal) {
          setItems((prev) => {
            let working = prev;
            // If there's a stale partial (no updates for >1.2s), finalize it implicitly and start a new bubble
            if (existing && now - existing.updatedAt > 1200) {
              working = working.map((it) => (it.id === existing.id ? { ...it, final: true } : it));
              partialStateRef.current[key] = undefined as any;
            }
            let id = existing && (!existing || now - existing.updatedAt <= 1200) ? existing.id : '';
            if (!id) {
              id = `t-${speaker}-${now}-${Math.random().toString(36).slice(2, 6)}`;
            }
            partialStateRef.current[key] = { id, updatedAt: now };
            const others = working.filter((it) => it.id !== id);
            return [...others, { id, speaker, text, final: false }];
          });
        } else {
          const id = existing?.id || `t-${speaker}-${now}-${Math.random().toString(36).slice(2, 6)}`;
          delete partialStateRef.current[key];
          setItems((prev) => {
            const others = prev.filter((it) => it.id !== id);
            return [...others, { id, speaker, text, final: true }];
          });
        }
      });

    })().catch((e) => {
      console.error("[fe] connect error:", e);
      setStatus("error");
    });

    return () => {
      isMounted = false;
      try {
        audioElsRef.current.forEach((el) => { el.pause(); el.srcObject = null; el.remove(); });
      } catch {}
      audioElsRef.current = [];
      roomRef.current?.disconnect();
      roomRef.current = null;
      trackToSpeakerRef.current = {};
      partialStateRef.current = {};
      try { onSpeakingChange?.(false); } catch {}
    };
  }, [roomName, identity]);

  const unlockAudio = async () => {
    try {
      await roomRef.current?.startAudio();
      for (const el of audioElsRef.current) { try { await el.play(); } catch {} }
      setNeedsUnlock(false);
    } catch {}
  };

  const leave = async () => {
    try {
      audioElsRef.current.forEach((el) => { try { el.pause(); } catch {}; el.srcObject = null; el.remove(); });
      audioElsRef.current = [];
      await roomRef.current?.disconnect();
    } finally {
      roomRef.current = null;
      setStatus("disconnected");
      try { onSpeakingChange?.(false); } catch {}
      onEnd?.();
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-md flex-col items-stretch gap-4">
      <div className="text-center text-xs text-muted-foreground">Status: {status}</div>
      {needsUnlock && (
        <Button variant="secondary" onClick={unlockAudio}>Enable audio</Button>
      )}
      <Transcript items={items} />
      {status === "connected" && (
        <div className="flex justify-center pt-2">
          <Button variant="destructive" onClick={leave}>End Call</Button>
        </div>
      )}
    </div>
  );
}
