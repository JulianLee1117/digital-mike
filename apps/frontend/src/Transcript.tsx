import { useEffect, useRef } from "react";
import { ScrollArea } from "./components/ui/scroll-area";

export function Transcript({ lines }: { lines: string[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);
  return (
    <ScrollArea className="h-64 rounded-md border bg-card">
      <div className="p-4">
        {lines.length === 0 ? (
          <div className="text-sm text-muted-foreground">No transcript yet…</div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="transcript-line my-1">
              {l}
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </ScrollArea>
  );
}
