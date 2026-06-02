import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { JobTable } from "@/components/jobs/JobTable";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { jobsApi, type JobListQuery } from "@/lib/api/jobs";
import type { JobStatus } from "@/lib/api/types";
import { useAuth } from "@/lib/auth/useAuth";

export const Route = createFileRoute("/jobs/")({
  component: () => (
    <RequireAuth>
      <JobsListPage />
    </RequireAuth>
  ),
});

const STATUS_OPTIONS: { value: JobStatus | ""; label: string }[] = [
  { value: "", label: "전체" },
  { value: "queued", label: "대기 중" },
  { value: "running", label: "실행 중" },
  { value: "success", label: "성공" },
  { value: "failed", label: "실패" },
  { value: "canceled", label: "취소됨" },
];

function JobsListPage() {
  const { hasRole } = useAuth();
  const [filters, setFilters] = useState<JobListQuery>({ mine: !hasRole("admin") });

  const { data, isLoading } = useQuery({
    queryKey: ["jobs", filters],
    queryFn: () => jobsApi.list(filters),
    refetchInterval: 5000,
  });

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">실행 이력</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            5초마다 자동 갱신됩니다.
          </p>
        </div>
        {hasRole("admin") && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setFilters({ ...filters, mine: !filters.mine })}
          >
            {filters.mine ? "전체 보기" : "내 작업만"}
          </Button>
        )}
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Input
          placeholder="앱 이름 또는 Job ID"
          value={filters.q ?? ""}
          onChange={(e) => setFilters({ ...filters, q: e.target.value, page: 1 })}
          className="max-w-xs"
        />
        <Select
          value={filters.status ?? "__all__"}
          onValueChange={(v) =>
            setFilters({
              ...filters,
              status: v === "__all__" ? undefined : (v as JobStatus),
              page: 1,
            })
          }
        >
          <SelectTrigger className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.value || "__all__"} value={o.value || "__all__"}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <JobTable jobs={data?.items} isLoading={isLoading} />
    </div>
  );
}
