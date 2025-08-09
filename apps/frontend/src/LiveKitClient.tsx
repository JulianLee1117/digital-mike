import React, { useEffect, useRef, useState } from "react";
import {
  Room, RoomEvent, Track, RemoteAudioTrack,
  setLogLevel, LogLevel,
} from "livekit-client";
import { getToken } from "./api";
import { Transcript } from "./Transcript";

setLogLevel(LogLevel.info);

export function LiveKitClient({ roomName, identity }: { roomName: string; identity: string }) {
  const [status, setStatus] = useState("disconnected");
  const [lines, setLines] = useState<string[]>([]);
  const [needsUnlock, setNeedsUnlock] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const audioElsRef = useRef<HTMLAudioElement[]>([]);

  useEffect(() => {
    let isMounted = true;
    (async () => {
      setStatus("connecting");
      const { token, url } = await getToken(roomName, identity);

      const room = new Room();
      roomRef.current = room;

      await room.connect(url, token, { autoSubscribe: true });
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
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) =>
        console.log("[fe] active speakers:", speakers.map(s => s.identity)),
      );

      // attach audio on subscribe
      room.on(RoomEvent.TrackSubscribed, async (track, _pub, participant) => {
        console.log("[fe] TrackSubscribed:", track.kind, "from", participant.identity);
        if (track.kind === Track.Kind.Audio) {
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

      // transcriptions (lk.transcription)
      room.registerTextStreamHandler("lk.transcription", async (reader, pinfo) => {
        const text = await reader.readAll();
        const attrs = (reader as any).info?.attributes as Record<string, string> | undefined;
        const isFinal = attrs?.["lk.transcription_final"] === "true";
        console.log("[fe] transcript:", { from: pinfo.identity, isFinal, text });
        if (isFinal) setLines((prev) => [...prev, text]);
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
    }
  };

  return (
    <div>
      <div>LiveKit: {status}</div>
      {needsUnlock && <button onClick={unlockAudio}>Enable audio</button>}
      {status === "connected" && <button onClick={leave}>Leave call</button>}
      <h3>Transcript</h3>
      <Transcript lines={lines} />
    </div>
  );
}
