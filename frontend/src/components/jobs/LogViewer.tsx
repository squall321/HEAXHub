import { useEffect, useRef, useState } from "react";
import { ChevronDown, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils/cn";

interface LogViewerProps {
  lines: string[];
  isConnected: boolean;
  height?: string;
}

export function LogViewer({ lines, isConnected, height = "60vh" }: LogViewerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [autoscroll, setAutoscroll] = useState(true);

  useEffect(() => {
    if (!autoscroll) return;
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [autoscroll, lines.length]);

  return (
    <div className="relative overflow-hidden rounded-lg border bg-[#0b1020] text-emerald-200">
      <div className="flex items-center justify-between border-b border-white/10 bg-black/30 px-3 py-1.5">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "h-2 w-2 rounded-full",
              isConnected ? "bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/80" : "bg-rose-500",
            )}
          />
          <span className="text-xs font-semibold text-white/80">
            {isConnected ? "라이브 스트림" : "연결 끊김"}
          </span>
          <span className="ml-2 font-mono text-[11px] text-white/40">{lines.length} lines</span>
        </div>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 text-white/70 hover:bg-white/10 hover:text-white"
          onClick={() => setAutoscroll((a) => !a)}
        >
          {autoscroll ? (
            <>
              <Pause className="mr-1.5 h-3.5 w-3.5" /> 자동스크롤
            </>
          ) : (
            <>
              <Play className="mr-1.5 h-3.5 w-3.5" /> 일시정지됨
            </>
          )}
        </Button>
      </div>
      <ScrollArea style={{ height }}>
        <div
          ref={ref}
          className="h-full overflow-y-auto px-4 py-3 font-mono text-[12px] leading-relaxed"
          style={{ maxHeight: height }}
          onScroll={(e) => {
            const el = e.currentTarget;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 20;
            if (atBottom && !autoscroll) setAutoscroll(true);
          }}
        >
          {lines.length === 0 ? (
            <div className="text-white/40">로그 대기 중…</div>
          ) : (
            lines.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap break-words">
                {line}
              </div>
            ))
          )}
        </div>
      </ScrollArea>
      {!autoscroll && (
        <button
          type="button"
          onClick={() => setAutoscroll(true)}
          className="absolute bottom-3 right-4 flex items-center gap-1 rounded-full bg-emerald-500/80 px-3 py-1 text-xs font-semibold text-white shadow-lg hover:bg-emerald-500"
        >
          <ChevronDown className="h-3 w-3" /> 최신으로
        </button>
      )}
    </div>
  );
}
