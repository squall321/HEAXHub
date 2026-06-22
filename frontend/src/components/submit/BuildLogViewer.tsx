import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { submissionsApi } from "@/lib/api/submissions";

/**
 * Shows the SIF build log for a submission (UX-03). Lets a submitter or admin
 * see why a build failed without ssh access. Pass `autoRefresh` while a build
 * is in progress so the log streams in without manual reloads.
 */
export function BuildLogViewer({
  submissionId,
  autoRefresh = false,
}: {
  submissionId: string;
  autoRefresh?: boolean;
}) {
  const { data, isLoading, isFetching, refetch, isError, error } = useQuery({
    queryKey: ["submission", submissionId, "build-log"],
    queryFn: () => submissionsApi.buildLog(submissionId),
    refetchInterval: autoRefresh ? 4000 : false,
  });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          빌드 로그
        </h4>
        <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          <span className="ml-1.5">새로고침</span>
        </Button>
      </div>
      {isLoading ? (
        <div className="flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> 로그를 불러오는 중…
        </div>
      ) : isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-3 text-sm text-destructive">
          로그를 불러오지 못했습니다: {error instanceof Error ? error.message : "알 수 없는 오류"}
        </div>
      ) : (
        <pre className="max-h-96 overflow-auto rounded-md border bg-zinc-950 px-3 py-3 text-[11px] leading-relaxed text-zinc-100">
          {data && data.trim().length > 0 ? data : "로그가 비어 있습니다."}
        </pre>
      )}
    </div>
  );
}
