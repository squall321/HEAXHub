import { animate, useMotionValue, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils/cn";
import { motionTiming } from "@/styles/tokens";

export interface StatusTotals {
  /** Total registered apps. */
  apps: number;
  /** Currently running services. */
  services: number;
  /** Jobs submitted today. */
  jobsToday: number;
  /** Free GPUs (we render as `free / total` when total is provided). */
  gpus: number;
  gpusTotal?: number;
}

interface StatusStripProps {
  totals: StatusTotals | null | undefined;
  className?: string;
}

/**
 * 4-tile metric band rendered below the hero ribbon.
 * Counters animate from 0 → target with ease-out (1.1s).
 * `prefers-reduced-motion` skips the animation entirely.
 */
export function StatusStrip({ totals, className }: StatusStripProps) {
  const loading = !totals;
  return (
    <section
      aria-label="포탈 상태"
      className={cn(
        "border-y border-border/60 bg-card/40 backdrop-blur-sm",
        className,
      )}
    >
      <dl
        className={cn(
          "mx-auto grid h-20 max-w-7xl grid-cols-2 divide-border/60 px-6 md:grid-cols-4 md:divide-x md:px-10",
        )}
      >
        <Tile
          label="등록된 앱"
          value={totals?.apps ?? 0}
          loading={loading}
          dotClass="bg-emerald-500"
        />
        <Tile
          label="실행 중"
          value={totals?.services ?? 0}
          loading={loading}
          dotClass="bg-amber-400"
        />
        <Tile
          label="오늘 잡"
          value={totals?.jobsToday ?? 0}
          loading={loading}
          dotClass="bg-sky-400"
        />
        <Tile
          label="사용가능 GPU"
          value={totals?.gpus ?? 0}
          loading={loading}
          dotClass="bg-violet-400"
          suffix={
            typeof totals?.gpusTotal === "number"
              ? ` / ${totals.gpusTotal}`
              : undefined
          }
        />
      </dl>
    </section>
  );
}

interface TileProps {
  label: string;
  value: number;
  loading: boolean;
  dotClass: string;
  suffix?: string;
}

function Tile({ label, value, loading, dotClass, suffix }: TileProps) {
  return (
    <div className="flex h-20 items-center gap-3 border-b border-border/60 px-4 last:border-b-0 md:border-b-0">
      <span
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          dotClass,
          "animate-pulse",
        )}
        aria-hidden="true"
      />
      <div className="min-w-0">
        <dd
          className="text-xl font-black tabular-nums leading-none text-foreground md:text-2xl"
          aria-live="polite"
        >
          {loading ? (
            <Skeleton className="h-6 w-12" />
          ) : (
            <>
              <CountUp value={value} />
              {suffix}
            </>
          )}
        </dd>
        <dt className="mt-1.5 truncate text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
          {label}
        </dt>
      </div>
    </div>
  );
}

interface CountUpProps {
  value: number;
  durationSec?: number;
}

function CountUp({ value, durationSec = motionTiming.counter }: CountUpProps) {
  const reduce = useReducedMotion();
  const mv = useMotionValue(0);
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (reduce) {
      setDisplay(value);
      return;
    }
    const unsub = mv.on("change", (latest) => setDisplay(Math.round(latest)));
    const controls = animate(mv, value, {
      duration: durationSec,
      ease: "easeOut",
    });
    return () => {
      controls.stop();
      unsub();
    };
  }, [value, durationSec, reduce, mv]);

  return <>{display.toLocaleString()}</>;
}
