import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronLeft, Download, RotateCcw, XCircle } from "lucide-react";
import { toast } from "sonner";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { JobTimeline } from "@/components/jobs/JobTimeline";
import { LogViewer } from "@/components/jobs/LogViewer";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { jobsApi } from "@/lib/api/jobs";
import { useJobLogs } from "@/lib/ws/useJobLogs";
import { formatBytes } from "@/lib/utils/format";

export const Route = createFileRoute("/jobs/$jobId")({
  component: () => (
    <RequireAuth>
      <JobDetailPage />
    </RequireAuth>
  ),
});

function JobDetailPage() {
  const { jobId } = Route.useParams();
  const qc = useQueryClient();

  const { data: job, isLoading } = useQuery({
    queryKey: ["jobs", jobId],
    queryFn: () => jobsApi.detail(jobId),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "queued" || status === "running" ? 3000 : false;
    },
  });

  const active = job?.status === "queued" || job?.status === "running";
  const { lines, isConnected } = useJobLogs(jobId, { enabled: active });

  const cancel = useMutation({
    mutationFn: () => jobsApi.cancel(jobId),
    onSuccess: () => {
      toast.success("취소 요청을 보냈습니다.");
      qc.invalidateQueries({ queryKey: ["jobs", jobId] });
    },
  });

  const rerun = useMutation({
    mutationFn: () => jobsApi.rerun(jobId),
    onSuccess: (res) => {
      toast.success(`새 작업 ${res.job_id} 시작`);
    },
  });

  if (isLoading || !job) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-8">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="mt-4 h-96 w-full" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
      <Link to="/jobs">
        <Button variant="ghost" size="sm" className="mb-4 -ml-2">
          <ChevronLeft className="mr-1 h-4 w-4" /> 실행 이력
        </Button>
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <code className="text-base font-mono font-bold">{job.id}</code>
            <JobStatusBadge status={job.status} />
          </div>
          <Link
            to="/apps/$appId"
            params={{ appId: job.app_id }}
            className="mt-1 inline-block text-lg font-semibold hover:underline"
          >
            {job.app_name ?? job.app_id}
          </Link>
        </div>

        <div className="flex gap-2">
          {active && (
            <Button variant="outline" size="sm" onClick={() => cancel.mutate()}>
              <XCircle className="mr-1.5 h-4 w-4" /> 취소
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => rerun.mutate()}>
            <RotateCcw className="mr-1.5 h-4 w-4" /> 재실행
          </Button>
        </div>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[300px_1fr]">
        <div className="space-y-4">
          <JobTimeline job={job} />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">파라미터</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="overflow-x-auto rounded-md bg-muted/40 p-3 text-xs">
                {JSON.stringify(job.params_json ?? {}, null, 2)}
              </pre>
            </CardContent>
          </Card>

          {job.input_files && job.input_files.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">입력 파일</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1 text-xs">
                  {job.input_files.map((p) => (
                    <li key={p} className="truncate font-mono text-muted-foreground">
                      {p}
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {job.output_files && job.output_files.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-sm">결과 파일</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {job.output_files.map((f) => (
                  <a
                    key={f.path}
                    href={jobsApi.fileUrl(job.id, f.path)}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between rounded-md border bg-card px-2.5 py-1.5 text-xs hover:bg-accent"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <Download className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">{f.name}</span>
                    </span>
                    <span className="text-muted-foreground">{formatBytes(f.size)}</span>
                  </a>
                ))}
              </CardContent>
            </Card>
          )}
        </div>

        <LogViewer lines={lines} isConnected={isConnected} />
      </div>

      {job.result_summary && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle className="text-sm">결과 요약</CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-md bg-muted/40 p-3 text-xs">
              {JSON.stringify(job.result_summary, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
