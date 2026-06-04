import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, History, RotateCw } from "lucide-react";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { Skeleton } from "@/components/ui/skeleton";
import { jobsApi } from "@/lib/api/jobs";
import type { Job } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/useAuth";
import { cn } from "@/lib/utils/cn";
import { timeAgo } from "@/lib/utils/format";
import { Rail } from "./Rail";

/**
 * "이어 작업하기" rail — horizontal cards of the user's recent jobs.
 * Hidden when not logged in or no jobs.
 */
export function RecentJobsRail() {
  const { isLoggedIn } = useAuth();
  const recent = useQuery({
    queryKey: ["jobs", "recent"],
    queryFn: () => jobsApi.list({ mine: true, page: 1, page_size: 8 }),
    enabled: isLoggedIn,
  });

  if (!isLoggedIn) return null;

  // Loading state shows skeleton row (so layout doesn't pop in).
  if (recent.isLoading) {
    return (
      <Rail
        title="이어 작업하기"
        icon={<History className="h-3.5 w-3.5" />}
        cta={{ to: "/jobs", label: "전체 이력" }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-[280px] shrink-0 sm:w-[320px]" />
        ))}
      </Rail>
    );
  }

  // Error: show a small inline notice with retry — don't silently hide.
  if (recent.isError) {
    return (
      <Rail
        title="이어 작업하기"
        icon={<History className="h-3.5 w-3.5" />}
      >
        <div
          role="alert"
          className={cn(
            "flex w-full items-center gap-3 rounded-xl border border-rose-500/30",
            "bg-rose-500/5 px-4 py-3 text-sm text-rose-200",
          )}
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="flex-1">최근 작업을 불러오지 못했습니다.</span>
          <button
            type="button"
            onClick={() => recent.refetch()}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border border-rose-400/40",
              "px-2 py-1 text-xs font-semibold",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            )}
            aria-label="최근 작업 다시 불러오기"
          >
            <RotateCw className="h-3 w-3" /> 재시도
          </button>
        </div>
      </Rail>
    );
  }

  const items = recent.data?.items ?? [];
  if (items.length === 0) return null;

  return (
    <Rail
      title="이어 작업하기"
      icon={<History className="h-3.5 w-3.5" />}
      cta={{ to: "/jobs", label: "전체 이력" }}
    >
      {items.map((job) => (
        <JobCard key={job.id} job={job} />
      ))}
    </Rail>
  );
}

function JobCard({ job }: { job: Job }) {
  return (
    <Link
      to="/jobs/$jobId"
      params={{ jobId: job.id }}
      className={cn(
        "group flex w-[280px] shrink-0 snap-start flex-col gap-2 sm:w-[320px]",
        "rounded-xl border bg-card p-4 transition-colors",
        "hover:border-foreground/20 hover:bg-accent/30",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <JobStatusBadge status={job.status} />
        <span className="text-[10px] text-muted-foreground">
          {timeAgo(job.started_at ?? job.created_at)}
        </span>
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold tracking-tight">
          {job.app_name ?? job.app_id}
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
          {job.id.slice(0, 12)}
        </div>
      </div>
      <div className="mt-auto flex items-center justify-between text-[11px] text-muted-foreground">
        <span className="truncate">{job.execution_target}</span>
        {job.duration_sec != null && (
          <span className="font-mono">{job.duration_sec}s</span>
        )}
      </div>
    </Link>
  );
}
