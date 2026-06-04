import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowRight, RotateCw, Shield } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { adminApi } from "@/lib/api/admin";
import { useAuth } from "@/lib/auth/useAuth";
import { cn } from "@/lib/utils/cn";

/**
 * Admin Glance — small KPI summary card only rendered for admins.
 * Replaces the inline 4-KPI block that used to sit inside the hero.
 */
export function AdminGlance() {
  const { hasRole } = useAuth();
  const isAdmin = hasRole("admin");

  const stats = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: () => adminApi.stats(),
    enabled: isAdmin,
  });

  if (!isAdmin) return null;

  return (
    <Card className="border-l-4 border-l-amber-400/80">
      <CardContent className="p-5">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <Shield className="h-3.5 w-3.5 text-amber-500" />
            관리자 글랜스
          </div>
          <Link
            to="/admin"
            className={cn(
              "inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              "rounded-md px-1",
            )}
          >
            관리자 콘솔 <ArrowRight className="h-3 w-3" />
          </Link>
        </div>

        {stats.isError ? (
          <div
            role="alert"
            className={cn(
              "flex items-center gap-3 rounded-lg border border-rose-500/30",
              "bg-rose-500/5 px-3 py-2 text-sm text-rose-200",
            )}
          >
            <AlertTriangle className="h-4 w-4 shrink-0" />
            <span className="flex-1">통계를 불러오지 못했습니다.</span>
            <button
              type="button"
              onClick={() => stats.refetch()}
              className={cn(
                "inline-flex items-center gap-1 rounded-md border border-rose-400/40",
                "px-2 py-1 text-xs font-semibold",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              )}
              aria-label="관리자 통계 다시 불러오기"
            >
              <RotateCw className="h-3 w-3" /> 재시도
            </button>
          </div>
        ) : stats.isLoading || !stats.data ? (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <KPI label="오늘 실행" value={stats.data.jobs_today} />
            <KPI label="활성 사용자" value={stats.data.active_users_today} />
            <KPI label="빌드 큐" value={stats.data.build_queue_depth} />
            <KPI
              label="신청 대기"
              value={stats.data.pending_submissions}
              tone={stats.data.pending_submissions > 0 ? "warn" : "default"}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function KPI({
  label,
  value,
  tone = "default",
}: {
  label: string;
  value: number;
  tone?: "default" | "warn";
}) {
  return (
    <div className="rounded-lg border bg-card/60 px-3 py-2.5">
      <div
        className={
          "text-xl font-black tabular-nums leading-none " +
          (tone === "warn" && value > 0
            ? "text-amber-600 dark:text-amber-400"
            : "text-foreground")
        }
      >
        {value.toLocaleString()}
      </div>
      <div className="mt-1.5 text-[10px] font-bold uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
    </div>
  );
}
