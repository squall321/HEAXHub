import { Link } from "@tanstack/react-router";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Compass, RotateCw, Sparkles, X } from "lucide-react";
import { useMemo } from "react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { AppSummary, AppStatus, AppType } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";
import { categoryLabel, colors } from "@/styles/tokens";
import { AppCard } from "./AppCard";

const CATEGORY_OPTIONS: AppType[] = [
  "cli_tool",
  "web_app",
  "windows_gui",
  "remote_app",
  "slurm_job",
  "container_app",
  "external_link",
];

const STATUS_OPTIONS: { value: AppStatus; label: string }[] = [
  { value: "stable", label: "안정" },
  { value: "beta", label: "베타" },
  { value: "draft", label: "초안" },
];

export type CatalogSort = "recent" | "name";

export interface CatalogGridProps {
  apps?: AppSummary[];
  isLoading?: boolean;
  /** When true, the grid renders an inline error with a retry button. */
  isError?: boolean;
  /** Callback wired to the retry button when `isError` is true. */
  onRetry?: () => void;
  activeTypes: AppType[];
  onTypesChange: (next: AppType[]) => void;
  activeStatuses: AppStatus[];
  onStatusesChange: (next: AppStatus[]) => void;
  sort: CatalogSort;
  onSortChange: (next: CatalogSort) => void;
  /** Optional title row.  If omitted, no header is rendered. */
  title?: string;
  subtitle?: string;
  catalogLinkLabel?: string;
}

export function CatalogGrid({
  apps,
  isLoading,
  isError,
  onRetry,
  activeTypes,
  onTypesChange,
  activeStatuses,
  onStatusesChange,
  sort,
  onSortChange,
  title,
  subtitle,
  catalogLinkLabel,
}: CatalogGridProps) {
  const toggleType = (t: AppType) =>
    onTypesChange(activeTypes.includes(t) ? activeTypes.filter((x) => x !== t) : [...activeTypes, t]);

  const toggleStatus = (s: AppStatus) =>
    onStatusesChange(
      activeStatuses.includes(s) ? activeStatuses.filter((x) => x !== s) : [...activeStatuses, s],
    );

  const hasFilter = activeTypes.length > 0 || activeStatuses.length > 0;

  const sorted = useMemo(() => {
    if (!apps) return undefined;
    const arr = [...apps];
    if (sort === "name") {
      arr.sort((a, b) => a.name.localeCompare(b.name));
    } else {
      arr.sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
      );
    }
    return arr;
  }, [apps, sort]);

  const resetFilters = () => {
    onTypesChange([]);
    onStatusesChange([]);
  };

  return (
    <section aria-label="앱 카탈로그" className="space-y-5">
      {(title || catalogLinkLabel) && (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Compass className="h-3.5 w-3.5" />
              둘러보기
            </div>
            {title && (
              <h2 className="mt-1 text-2xl font-bold tracking-tight">{title}</h2>
            )}
            {subtitle && (
              <p className="mt-0.5 text-sm text-muted-foreground">{subtitle}</p>
            )}
          </div>
          {catalogLinkLabel && (
            <Link to="/apps">
              <Button variant="outline" size="sm">
                {catalogLinkLabel}
              </Button>
            </Link>
          )}
        </div>
      )}

      <div
        className="flex flex-wrap items-center gap-2 border-b border-border/60 pb-4"
        role="group"
        aria-label="카탈로그 필터"
      >
        {CATEGORY_OPTIONS.map((t) => {
          const active = activeTypes.includes(t);
          const c = colors.category[t];
          return (
            <button
              key={t}
              type="button"
              aria-pressed={active}
              aria-label={`${categoryLabel[t]} 필터 ${active ? "해제" : "적용"}`}
              onClick={() => toggleType(t)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              )}
              style={{
                background: active ? `${c}22` : "transparent",
                borderColor: active ? c : "var(--border)",
                color: active ? c : undefined,
              }}
            >
              {categoryLabel[t]}
            </button>
          );
        })}

        <span className="mx-1 h-4 w-px bg-border" aria-hidden />

        {STATUS_OPTIONS.map((s) => {
          const active = activeStatuses.includes(s.value);
          return (
            <button
              key={s.value}
              type="button"
              aria-pressed={active}
              aria-label={`${s.label} 필터 ${active ? "해제" : "적용"}`}
              onClick={() => toggleStatus(s.value)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
                active
                  ? "border-foreground bg-foreground/10 text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
              )}
            >
              {s.label}
            </button>
          );
        })}

        <div className="ml-auto flex items-center gap-2">
          {hasFilter && (
            <Button
              variant="ghost"
              size="sm"
              onClick={resetFilters}
              className="h-8 px-2 text-xs"
            >
              <X className="mr-1 h-3 w-3" /> 필터 초기화
            </Button>
          )}
          <Select value={sort} onValueChange={(v) => onSortChange(v as CatalogSort)}>
            <SelectTrigger className="h-8 w-[120px] text-xs" aria-label="정렬">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="recent">최근 업데이트</SelectItem>
              <SelectItem value="name">이름순</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {isError ? (
        <ErrorState onRetry={onRetry} />
      ) : isLoading ? (
        <CatalogSkeleton />
      ) : !sorted || sorted.length === 0 ? (
        <EmptyState onReset={hasFilter ? resetFilters : undefined} />
      ) : (
        <AnimatePresence mode="popLayout">
          <motion.div
            key={`${activeTypes.join("|")}-${activeStatuses.join("|")}-${sort}`}
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          >
            {sorted.map((app, i) => (
              <motion.div
                key={app.id}
                layout
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{
                  delay: Math.min(i, 11) * 0.06,
                  duration: 0.28,
                  ease: "easeOut",
                }}
              >
                <AppCard app={app} />
              </motion.div>
            ))}
          </motion.div>
        </AnimatePresence>
      )}
    </section>
  );
}

function CatalogSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <Skeleton key={i} className="h-44 w-full rounded-2xl" />
      ))}
    </div>
  );
}

function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="rounded-2xl border border-rose-500/30 bg-rose-500/5 px-6 py-12 text-center"
    >
      <AlertTriangle className="mx-auto h-7 w-7 text-rose-300" />
      <p className="mt-3 text-sm font-medium text-rose-200">
        카탈로그를 불러오지 못했습니다.
      </p>
      <p className="mt-1 text-xs text-rose-200/80">
        잠시 후 다시 시도해주세요.
      </p>
      {onRetry && (
        <div className="mt-5 flex items-center justify-center">
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            aria-label="카탈로그 다시 불러오기"
          >
            <RotateCw className="mr-1 h-3 w-3" /> 재시도
          </Button>
        </div>
      )}
    </div>
  );
}

function EmptyState({ onReset }: { onReset?: () => void }) {
  return (
    <div className="rounded-2xl border border-dashed bg-card/40 px-6 py-16 text-center">
      <Sparkles className="mx-auto h-7 w-7 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium">선택한 조건에 맞는 앱이 없어요.</p>
      <p className="mt-1 text-xs text-muted-foreground">
        필터를 조정하거나 전체 카탈로그에서 다른 앱을 살펴보세요.
      </p>
      <div className="mt-5 flex items-center justify-center gap-2">
        {onReset && (
          <Button variant="outline" size="sm" onClick={onReset}>
            필터 초기화
          </Button>
        )}
        <Link to="/apps">
          <Button size="sm">전체 카탈로그 보기</Button>
        </Link>
      </div>
    </div>
  );
}
