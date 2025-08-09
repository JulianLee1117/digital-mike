import { useEffect, useRef } from "react";
import { ScrollArea } from "./components/ui/scroll-area";

export type TranscriptItem = {
  id: string;
  speaker: "user" | "mike";
  text: string;
  final?: boolean;
};

export function Transcript({ items }: { items: TranscriptItem[] }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items]);
  return (
    <ScrollArea className="h-64 rounded-md border bg-card">
      <div className="space-y-3 p-4">
        {items.length === 0 ? (
          <div className="text-sm text-muted-foreground">No transcript yet…</div>
        ) : (
          items.map((seg) => (
            <div key={seg.id} className={`flex ${seg.speaker === "user" ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm shadow-sm ${
                  seg.speaker === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-secondary text-secondary-foreground"
                } ${seg.final ? "opacity-100" : "opacity-90"}`}
              >
                <div className="text-[10px] font-medium opacity-70">
                  {seg.speaker === "user" ? "You" : "Mike"}
                </div>
                <div className="whitespace-pre-wrap leading-relaxed">{seg.text}</div>
              </div>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </ScrollArea>
  );
}
