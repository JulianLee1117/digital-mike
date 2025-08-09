import React, { useState } from "react";
import { LiveKitClient } from "./LiveKitClient";

export default function App() {
  const [started, setStarted] = useState(false);
  const room = "digital-mike";
  const identity = `user-${Math.random().toString(36).slice(2, 8)}`;
  return (
    <div style={{ padding: 16, fontFamily: "sans-serif" }}>
      {!started ? (
        <button onClick={() => setStarted(true)}>Start Call</button>
      ) : (
        <LiveKitClient roomName={room} identity={identity} />
      )}
    </div>
  );
}
