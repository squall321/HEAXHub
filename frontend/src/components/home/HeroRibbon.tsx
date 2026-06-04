import { Link } from "@tanstack/react-router";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, LayoutGrid, Plus, Search, Shield } from "lucide-react";
import { BrandLogo } from "@/components/common/BrandLogo";
import { cn } from "@/lib/utils/cn";
import {
  heroAuroraA,
  heroAuroraB,
  heroAuroraC,
  heroGradient,
  motionEase,
  motionTiming,
} from "@/styles/tokens";

interface HeroRibbonProps {
  /** Opens the global CommandDialog search. */
  onSearchOpen?: () => void;
  /** Renders the admin-console quick action when true. */
  isAdmin?: boolean;
  className?: string;
}

/**
 * Sticky hero band: aurora gradient backdrop, brand logo, strapline,
 * search trigger, and 3 quick-action chips (admin chip is optional).
 *
 * Responsive heights: lg 380 / md 280 / sm 220.
 */
export function HeroRibbon({ onSearchOpen, isAdmin = false, className }: HeroRibbonProps) {
  const reduce = useReducedMotion();

  return (
    <section
      className={cn(
        "relative isolate overflow-hidden",
        "h-[220px] sm:h-[220px] md:h-[280px] lg:h-[380px]",
        className,
      )}
      style={{ background: heroGradient }}
      aria-label="HEAXHub 소개"
    >
      {/* Aurora layer — radial blobs that slowly drift */}
      <div className="pointer-events-none absolute inset-0 -z-0">
        <AuroraBlob
          gradient={heroAuroraA}
          delay={0}
          duration={motionTiming.auroraDriftSec}
          reduce={!!reduce}
        />
        <AuroraBlob
          gradient={heroAuroraB}
          delay={-4}
          duration={motionTiming.auroraDriftSec + 4}
          reduce={!!reduce}
        />
        <AuroraBlob
          gradient={heroAuroraC}
          delay={-9}
          duration={motionTiming.auroraDriftSec + 8}
          reduce={!!reduce}
        />
      </div>

      {/* Subtle grain overlay (inline SVG so we don't rely on a static asset) */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 mix-blend-overlay opacity-[0.05]"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)' opacity='0.55'/></svg>\")",
        }}
      />

      {/* Content */}
      <div className="relative z-10 mx-auto flex h-full max-w-7xl flex-col justify-center gap-3 px-6 md:gap-4 md:px-10 lg:px-16">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: motionTiming.hero, ease: motionEase.easeOut }}
          className="flex max-w-3xl flex-col gap-3 md:gap-4"
        >
          {/* Eyebrow */}
          <div className="flex items-center gap-3 text-[10px] font-bold uppercase tracking-[0.25em] text-amber-300 md:text-[11px]">
            <span className="block h-px w-7 bg-amber-300" />
            AI Automation Portal
          </div>

          {/* Brand logo */}
          <BrandLogo size="lg" className="block" />

          {/* Strapline (responsive sizing) */}
          <div className="space-y-1.5 md:space-y-2">
            <h1 className="text-xl font-black leading-[1.1] tracking-tight text-white md:text-[28px] lg:text-[36px]">
              흩어진 자동화를 <span className="text-amber-300">한 곳에서</span>.
            </h1>
            <p className="hidden text-sm text-white/70 md:block md:text-[15px] lg:text-base">
              ~20개 스택, 신청 → 빌드 → 공개까지 자동.
            </p>
          </div>

          {/* Search button — opens CommandDialog */}
          <div className="flex w-full max-w-2xl items-center gap-2">
            <button
              type="button"
              onClick={onSearchOpen}
              aria-label="앱 검색 열기 (Ctrl/Cmd + K)"
              className={cn(
                "group flex h-12 flex-1 items-center gap-3 rounded-xl",
                "border border-white/15 bg-white/[0.07] px-4",
                "text-left text-sm text-white/80 backdrop-blur-md",
                "transition-colors hover:bg-white/[0.12]",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-[#020617]",
                "md:h-12 lg:h-14 lg:text-base",
              )}
            >
              <Search className="h-4 w-4 shrink-0 text-white/60 md:h-5 md:w-5" />
              <span className="flex-1 truncate">앱 이름·태그·스택 검색</span>
              <kbd className="hidden rounded border border-white/20 bg-white/10 px-1.5 py-0.5 font-mono text-[10px] font-bold text-white/80 md:inline-block">
                ⌘K
              </kbd>
              <ArrowRight className="h-4 w-4 shrink-0 text-white/40 transition-transform group-hover:translate-x-0.5" />
            </button>
          </div>

          {/* Quick action chips */}
          <div className="flex flex-wrap items-center gap-2">
            <QuickChip to="/apps" icon={<LayoutGrid className="h-3.5 w-3.5" />}>
              앱 카탈로그
            </QuickChip>
            <QuickChip to="/submit" icon={<Plus className="h-3.5 w-3.5" />}>
              새 앱 신청
            </QuickChip>
            {isAdmin && (
              <QuickChip to="/admin" icon={<Shield className="h-3.5 w-3.5" />}>
                관리자 콘솔
              </QuickChip>
            )}
          </div>
        </motion.div>
      </div>
    </section>
  );
}

interface AuroraBlobProps {
  gradient: string;
  delay: number;
  duration: number;
  reduce: boolean;
}

function AuroraBlob({ gradient, delay, duration, reduce }: AuroraBlobProps) {
  if (reduce) {
    return (
      <div
        aria-hidden="true"
        className="absolute inset-0"
        style={{ backgroundImage: gradient }}
      />
    );
  }
  return (
    <motion.div
      aria-hidden="true"
      className="absolute inset-0"
      style={{ backgroundImage: gradient, mixBlendMode: "screen" }}
      animate={{
        x: ["0%", "-2%", "0%"],
        y: ["0%", "1.2%", "0%"],
      }}
      transition={{
        duration,
        delay,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    />
  );
}

interface QuickChipProps {
  to: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}

function QuickChip({ to, icon, children }: QuickChipProps) {
  return (
    <Link
      to={to}
      aria-label={`${children?.toString() ?? ""}로 이동`}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full",
        "border border-white/15 bg-white/[0.06] px-3.5 py-1.5",
        "text-[11px] font-bold text-white/95 md:text-xs",
        "transition-colors hover:bg-white/[0.12]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-[#020617]",
      )}
    >
      {icon}
      {children}
    </Link>
  );
}
