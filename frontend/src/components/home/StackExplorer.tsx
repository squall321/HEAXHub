/**
 * Stack Explorer — signature centerpiece on the home page.
 *
 * Layout:
 *   - Section header with stack count
 *   - Capability tab strip (6 tabs, active uses capability gradient)
 *   - Animated panel showing stack cards for the active capability
 *
 * Behavior:
 *   - Active capability is internal state (initial = "web_service")
 *   - Clicking a StackCard navigates to /apps?stack=<key>
 *   - Reduced-motion users skip enter/exit animations
 */

import { Link } from "@tanstack/react-router";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Layers } from "lucide-react";
import { useMemo, useState } from "react";
import { stacksByCapability, type StackDef, stackCount } from "@/lib/stacks";
import {
  capability as capabilityColors,
  capabilityGradient,
  capabilityLabel,
  type CapabilityKey,
} from "@/styles/tokens";

const CAPABILITY_ORDER: CapabilityKey[] = [
  "web_service",
  "data_dash",
  "batch_job",
  "external_int",
  "static_host",
  "desktop",
];

export function StackExplorer() {
  const [active, setActive] = useState<CapabilityKey>("web_service");
  const grouped = useMemo(() => stacksByCapability(), []);
  const counts = useMemo(() => {
    const out = {} as Record<CapabilityKey, number>;
    for (const k of CAPABILITY_ORDER) out[k] = grouped[k].length;
    return out;
  }, [grouped]);
  const reduceMotion = useReducedMotion();

  const total = stackCount();

  const onTabKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>, idx: number) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const delta = e.key === "ArrowRight" ? 1 : -1;
      const next = (idx + delta + CAPABILITY_ORDER.length) % CAPABILITY_ORDER.length;
      setActive(CAPABILITY_ORDER[next]);
      const tabs = e.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>(
        "[role=tab]",
      );
      tabs?.[next]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(CAPABILITY_ORDER[0]);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(CAPABILITY_ORDER[CAPABILITY_ORDER.length - 1]);
    }
  };

  return (
    <section className="mx-auto w-full max-w-7xl px-6 md:px-10">
      <div className="mb-5 flex items-end justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Layers className="h-3.5 w-3.5" />
            Stack Explorer
          </div>
          <h2 className="mt-1 text-2xl font-bold tracking-tight">
            이런 스택을 지원합니다 — {total}개
          </h2>
        </div>
      </div>

      {/* Tab strip — horizontally scrollable on mobile, wraps on md+ */}
      <div
        role="tablist"
        aria-label="지원 스택 카테고리"
        className="mb-5 -mx-6 flex gap-2 overflow-x-auto px-6 pb-1 md:mx-0 md:flex-wrap md:overflow-visible md:px-0 md:pb-0"
      >
        {CAPABILITY_ORDER.map((k, idx) => {
          const isActive = k === active;
          return (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={isActive}
              aria-controls={`stack-panel-${k}`}
              id={`stack-tab-${k}`}
              tabIndex={isActive ? 0 : -1}
              onClick={() => setActive(k)}
              onKeyDown={(e) => onTabKeyDown(e, idx)}
              className={
                "shrink-0 rounded-full border px-4 py-2 text-sm font-semibold transition-colors " +
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background " +
                (isActive
                  ? "border-transparent text-white shadow-lg"
                  : "border-border bg-card text-muted-foreground hover:text-foreground")
              }
              style={isActive ? { background: capabilityGradient(k) } : undefined}
            >
              {capabilityLabel[k]}
              <span
                className={
                  "ml-2 rounded-full px-1.5 py-0.5 text-[10px] font-bold " +
                  (isActive ? "bg-white/20 text-white" : "bg-muted text-muted-foreground")
                }
              >
                {counts[k]}
              </span>
            </button>
          );
        })}
      </div>

      {/* Panel */}
      <div className="relative overflow-hidden rounded-2xl">
        <AnimatePresence mode="wait">
          <motion.div
            key={active}
            id={`stack-panel-${active}`}
            role="tabpanel"
            aria-labelledby={`stack-tab-${active}`}
            tabIndex={0}
            initial={reduceMotion ? false : { opacity: 0, x: 24 }}
            animate={{ opacity: 1, x: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, x: -16 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            className="relative overflow-hidden rounded-2xl p-6 md:p-8 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            style={{ background: capabilityGradient(active) }}
          >
            {/* readability overlay */}
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-black/20"
            />
            <div className="relative z-10 grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {grouped[active].map((stack) => (
                <StackCard key={stack.key} stack={stack} capability={active} />
              ))}
            </div>
          </motion.div>
        </AnimatePresence>
      </div>
    </section>
  );
}

function StackCard({
  stack,
  capability: cap,
}: {
  stack: StackDef;
  capability: CapabilityKey;
}) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.div
      whileHover={reduceMotion ? undefined : { y: -3, transition: { duration: 0.18 } }}
    >
      <Link
        to="/apps"
        search={{ stack: stack.key } as never}
        aria-label={`${stack.label} — ${stack.tagline}, 예제 ${stack.examples}개`}
        className={
          "group relative block rounded-xl border border-white/15 bg-white/[0.08] " +
          "p-4 backdrop-blur-md transition-colors hover:bg-white/[0.14] " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        }
      >
        <span
          className="inline-flex h-11 w-11 items-center justify-center rounded-lg bg-black/30 text-lg font-black tracking-tighter text-white"
          style={{ boxShadow: `inset 0 0 0 1px ${capabilityColors[cap].base}55` }}
        >
          {stack.monogram}
        </span>
        <div className="mt-3 text-sm font-bold text-white">{stack.label}</div>
        <div className="mt-1 line-clamp-1 text-xs text-white/70">{stack.tagline}</div>
        <span
          className="absolute right-3 top-3 rounded-full bg-white/15 px-2 py-0.5 text-[10px] font-bold text-white"
          aria-hidden
        >
          {stack.examples}
        </span>
      </Link>
    </motion.div>
  );
}
