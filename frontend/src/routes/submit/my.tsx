import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Send } from "lucide-react";
import { useState } from "react";
import { RequireAuth } from "@/components/common/RequireAuth";
import { BuildLogViewer } from "@/components/submit/BuildLogViewer";
import { ManifestPreview } from "@/components/apps/ManifestPreview";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { submissionsApi } from "@/lib/api/submissions";
import type { Submission, SubmissionStatus } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";

type BadgeVariant = "success" | "warning" | "info" | "destructive" | "muted";

const STATUS_META: Record<SubmissionStatus, { label: string; variant: BadgeVariant }> = {
  pending: { label: "대기", variant: "warning" },
  under_review: { label: "검토 중", variant: "warning" },
  manifest_required: { label: "매니페스트 보완 필요", variant: "warning" },
  approved: { label: "승인됨", variant: "info" },
  rejected: { label: "반려됨", variant: "destructive" },
  provisioning: { label: "준비 중", variant: "info" },
  building: { label: "빌드 중", variant: "info" },
  built: { label: "빌드 완료", variant: "success" },
  published: { label: "공개됨", variant: "success" },
  failed: { label: "실패", variant: "destructive" },
};

// 빌드 로그가 의미를 갖는 상태(빌드를 시도했거나 끝난 경우).
const LOG_RELEVANT: SubmissionStatus[] = [
  "provisioning",
  "building",
  "built",
  "published",
  "failed",
];

export const Route = createFileRoute("/submit/my")({
  component: () => (
    <RequireAuth>
      <MySubmissionsPage />
    </RequireAuth>
  ),
});

function MySubmissionsPage() {
  const [selected, setSelected] = useState<Submission | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["submissions", "mine"],
    queryFn: () => submissionsApi.list({ mine: true }),
    refetchInterval: 5000,
  });

  const items = data?.items ?? [];

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 md:px-10">
      <header className="mb-6 flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">내 신청</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            내가 등록한 앱의 검토·빌드 상태와 빌드 로그를 확인합니다. 5초마다 자동 갱신됩니다.
          </p>
        </div>
        <Button asChild size="sm" variant="outline">
          <Link to="/submit">
            <Send className="mr-1.5 h-4 w-4" /> 새 앱 신청
          </Link>
        </Button>
      </header>

      {isLoading ? (
        <Skeleton className="h-96 w-full" />
      ) : (
        <div className="rounded-lg border bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>이름</TableHead>
                <TableHead>앱 ID</TableHead>
                <TableHead>상태</TableHead>
                <TableHead>접수</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((s) => {
                const meta = STATUS_META[s.status] ?? { label: s.status, variant: "muted" as const };
                return (
                  <TableRow
                    key={s.id}
                    className="cursor-pointer"
                    onClick={() => setSelected(s)}
                  >
                    <TableCell className="text-sm font-medium">{s.name}</TableCell>
                    <TableCell>
                      <code className="text-xs">{s.proposed_app_id}</code>
                    </TableCell>
                    <TableCell>
                      <Badge variant={meta.variant}>{meta.label}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDateTime(s.created_at)}
                    </TableCell>
                  </TableRow>
                );
              })}
              {items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="py-12 text-center text-sm text-muted-foreground">
                    아직 신청한 앱이 없습니다. 우측 상단 “새 앱 신청”에서 등록하세요.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <Sheet open={Boolean(selected)} onOpenChange={(o) => !o && setSelected(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
          {selected && (
            <>
              <SheetHeader>
                <SheetTitle>{selected.name}</SheetTitle>
                <SheetDescription>{selected.upstream_repo_url}</SheetDescription>
              </SheetHeader>

              <div className="mt-6 space-y-5">
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <Spec label="제안 ID">
                    <code>{selected.proposed_app_id}</code>
                  </Spec>
                  <Spec label="상태">
                    <Badge variant={(STATUS_META[selected.status]?.variant) ?? "muted"}>
                      {STATUS_META[selected.status]?.label ?? selected.status}
                    </Badge>
                  </Spec>
                  <Spec label="접수">{formatDateTime(selected.created_at)}</Spec>
                  {selected.published_at && (
                    <Spec label="공개">{formatDateTime(selected.published_at)}</Spec>
                  )}
                </div>

                {selected.description && (
                  <div className="rounded-md border bg-muted/40 p-3 text-sm">
                    {selected.description}
                  </div>
                )}

                {selected.review_notes && (
                  <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
                    <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      검토 메모
                    </div>
                    {selected.review_notes}
                  </div>
                )}

                {selected.status === "published" && (
                  <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm">
                    공개됨 — 카탈로그와 <code>/apps/{selected.proposed_app_id}/</code> 에서 확인하세요.
                  </p>
                )}

                {selected.proposed_manifest && (
                  <ManifestPreview manifest={selected.proposed_manifest} />
                )}

                {LOG_RELEVANT.includes(selected.status) && (
                  <BuildLogViewer
                    submissionId={selected.id}
                    autoRefresh={
                      selected.status === "provisioning" || selected.status === "building"
                    }
                  />
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Spec({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className="mt-1">{children}</div>
    </div>
  );
}
