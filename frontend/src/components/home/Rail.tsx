import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

interface RailProps {
  title: string;
  subtitle?: string;
  cta?: { to: string; label: string };
  icon?: ReactNode;
  /** When provided, the rail mounts only if children rendered. */
  className?: string;
  children: ReactNode;
}

/**
 * Generic horizontal rail used by RecentJobsRail / FavoritesRail / etc.
 *
 * - Mobile / sm: horizontal scroll with snap-x snap-mandatory, full-bleed.
 * - lg: still scroll, but children width naturally fills.
 * - CTA "더 보기 →" pinned to the right of the header.
 *
 * Each child should be a fixed-width card (w-[280px] sm:w-[320px]).
 */
export function Rail({ title, subtitle, cta, icon, className, children }: RailProps) {
  return (
    <section className={cn("space-y-4", className)} aria-label={title}>
      <div className="flex items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {icon}
            {title}
          </div>
          {subtitle && (
            <p className="mt-1 truncate text-sm text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {cta && (
          <Link
            to={cta.to}
            className={cn(
              "inline-flex shrink-0 items-center gap-1 rounded-full",
              "border border-border bg-card px-3 py-1 text-xs font-semibold",
              "text-muted-foreground transition-colors hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            )}
            aria-label={`${title} — ${cta.label}`}
          >
            {cta.label}
            <ArrowRight className="h-3 w-3" />
          </Link>
        )}
      </div>

      <div
        className={cn(
          "flex gap-4 overflow-x-auto pb-2",
          "snap-x snap-mandatory scrollbar-thin",
          // Full-bleed feel on mobile: extend out of section padding.
          "-mx-6 px-6 md:-mx-10 md:px-10",
        )}
      >
        {children}
      </div>
    </section>
  );
}
