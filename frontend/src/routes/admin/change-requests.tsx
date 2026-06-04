import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { changeRequestsApi } from "@/lib/api/changeRequests";
import type { ChangeRequestStatus } from "@/lib/api/types";
import { statusColor, statusLabel } from "@/lib/utils/confidence";
import { formatDateTime } from "@/lib/utils/format";

export const Route = createFileRoute("/admin/change-requests")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminChangeRequestsPage />
    </RequireAuth>
  ),
});

const STATUSES: { value: ChangeRequestStatus | "all"; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "draft", label: "초안" },
  { value: "awaiting_assistant", label: "Claude 응답 대기" },
  { value: "assistant_responded", label: "검토 가능" },
  { value: "issued_md", label: "Markdown 발행" },
  { value: "issued_pr", label: "PR 발행" },
  { value: "issued_issue", label: "Issue 발행" },
  { value: "merged", label: "병합 완료" },
  { value: "rejected", label: "거절" },
  { value: "superseded", label: "대체됨" },
];

function AdminChangeRequestsPage() {
  const [status, setStatus] = useState<ChangeRequestStatus | "all">("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["change-requests", { status }],
    queryFn: () =>
      changeRequestsApi.list(status === "all" ? undefined : { status }),
  });

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">변경 요청</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            AI가 생성한 manifest 제안 + PR 발행 이력.
          </p>
        </div>
        <div className="w-48">
          <Select value={status} onValueChange={(v) => setStatus(v as ChangeRequestStatus | "all")}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUSES.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </header>

      <div className="mt-6">
        {isLoading ? (
          <Skeleton className="h-96 w-full" />
        ) : error ? (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
            <p className="font-medium">아직 사용할 수 없습니다.</p>
            <p className="mt-1 text-muted-foreground">
              백엔드 `/change-requests` 엔드포인트가 준비되면 표시됩니다.
            </p>
          </div>
        ) : (
          <div className="rounded-lg border bg-card">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>ID</TableHead>
                  <TableHead>저장소</TableHead>
                  <TableHead>App ID</TableHead>
                  <TableHead>상태</TableHead>
                  <TableHead>PR</TableHead>
                  <TableHead>생성</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.items ?? []).map((cr) => (
                  <TableRow key={cr.id} className="cursor-pointer">
                    <TableCell className="font-mono text-xs">
                      <Link
                        to={"/admin/change-requests/$crId" as never}
                        params={{ crId: cr.id } as never}
                        className="hover:underline"
                      >
                        {cr.id?.slice(0, 8) ?? "—"}
                      </Link>
                    </TableCell>
                    <TableCell className="max-w-[28ch] truncate font-mono text-xs text-muted-foreground">
                      {cr.repo_url}
                    </TableCell>
                    <TableCell className="text-xs">{cr.app_id ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant={statusColor(cr.status)}>
                        {statusLabel(cr.status)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {cr.pr_url ? (
                        <a
                          href={cr.pr_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                          onClick={(e) => e.stopPropagation()}
                        >
                          열기 <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(cr.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
                {(data?.items ?? []).length === 0 && (
                  <TableRow>
                    <TableCell colSpan={6} className="py-12 text-center text-sm text-muted-foreground">
                      해당 상태의 변경 요청이 없습니다.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}

