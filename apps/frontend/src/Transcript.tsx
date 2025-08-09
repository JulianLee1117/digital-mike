import { useEffect, useRef } from "react";
export function Transcript({ lines }: { lines: string[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [lines]);
  return (
    <div style={{border:"1px solid #ddd", padding:12, height:220, overflow:"auto", borderRadius:8}}>
      {lines.length === 0 ? <div style={{opacity:.6}}>No transcript yet…</div> :
        lines.map((l, i) => <div key={i} style={{margin:"6px 0"}}>{l}</div>)
      }
      <div ref={endRef} />
    </div>
  );
}
