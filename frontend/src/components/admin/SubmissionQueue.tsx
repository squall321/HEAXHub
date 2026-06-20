import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
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
import { Textarea } from "@/components/ui/textarea";
import { submissionsApi } from "@/lib/api/submissions";
import type { Submission } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";
import { ManifestPreview } from "@/components/apps/ManifestPreview";

export function SubmissionQueue() {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<Submission | null>(null);
  const [notes, setNotes] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "submissions"],
    queryFn: () => submissionsApi.list(),
  });

  const approve = useMutation({
    mutationFn: () => submissionsApi.approve(selected!.id, notes),
    onSuccess: () => {
      toast.success("신청이 승인되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "submissions"] });
      setSelected(null);
      setNotes("");
    },
  });

  const reject = useMutation({
    mutationFn: () => submissionsApi.reject(selected!.id, notes),
    onSuccess: () => {
      toast.success("신청이 반려되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "submissions"] });
      setSelected(null);
      setNotes("");
    },
  });

  const testRun = useMutation({
    mutationFn: () => submissionsApi.testRun(selected!.id),
    onSuccess: (res) => {
      toast.success(`테스트 실행을 시작했습니다 (job ${res.job_id.slice(0, 8)}).`);
      qc.invalidateQueries({ queryKey: ["admin", "submissions"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "테스트 실행 실패"),
  });

  const publish = useMutation({
    mutationFn: () => submissionsApi.publish(selected!.id),
    onSuccess: () => {
      toast.success("공개되었습니다. 카탈로그에 노출됩니다.");
      qc.invalidateQueries({ queryKey: ["admin", "submissions"] });
      setSelected(null);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "공개 실패"),
  });

  const retry = useMutation({
    mutationFn: () => submissionsApi.retry(selected!.id),
    onSuccess: () => {
      toast.success("재시도를 시작했습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "submissions"] });
      setSelected(null);
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "재시도 실패"),
  });

  if (isLoading) return <Skeleton className="h-96 w-full" />;

  const items = data?.items ?? [];

  return (
    <>
      <div className="rounded-lg border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>신청자</TableHead>
              <TableHead>앱 ID</TableHead>
              <TableHead>이름</TableHead>
              <TableHead>Git URL</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>접수</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((s) => (
              <TableRow
                key={s.id}
                className="cursor-pointer"
                onClick={() => {
                  setSelected(s);
                  setNotes(s.review_notes ?? "");
                }}
              >
                <TableCell className="text-sm">
                  {s.submitter_display ?? s.submitter_user_id}
                </TableCell>
                <TableCell>
                  <code className="text-xs">{s.proposed_app_id}</code>
                </TableCell>
                <TableCell className="text-sm font-medium">{s.name}</TableCell>
                <TableCell className="max-w-[20ch] truncate text-xs text-muted-foreground">
                  {s.upstream_repo_url}
                </TableCell>
                <TableCell>
                  <Badge variant={s.status === "pending" ? "warning" : "muted"}>{s.status}</Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(s.created_at)}
                </TableCell>
              </TableRow>
            ))}
            {items.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="py-12 text-center text-sm text-muted-foreground">
                  대기 중인 신청이 없습니다.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

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
                    <Badge variant="warning">{selected.status}</Badge>
                  </Spec>
                  <Spec label="신청자">{selected.submitter_display ?? "—"}</Spec>
                  <Spec label="접수">{formatDateTime(selected.created_at)}</Spec>
                </div>

                {selected.description && (
                  <div className="rounded-md border bg-muted/40 p-3 text-sm">
                    {selected.description}
                  </div>
                )}

                {selected.proposed_manifest && (
                  <ManifestPreview manifest={selected.proposed_manifest} />
                )}

                <div>
                  <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    검토 메모
                  </label>
                  <Textarea
                    rows={3}
                    value={notes}
                    onChange={(e) => setNotes(e.target.value)}
                    placeholder="승인/반려 사유 또는 추가 안내"
                  />
                </div>

                {/* 상태별 액션 — 승인→빌드 대기→테스트→공개를 웹에서 완주 */}
                {(selected.status === "pending" ||
                  selected.status === "under_review" ||
                  selected.status === "manifest_required") && (
                  <div className="flex gap-2">
                    <Button
                      onClick={() => approve.mutate()}
                      disabled={approve.isPending || reject.isPending}
                      className="flex-1"
                    >
                      <Check className="mr-2 h-4 w-4" /> 승인
                    </Button>
                    <Button
                      variant="destructive"
                      onClick={() => reject.mutate()}
                      disabled={approve.isPending || reject.isPending}
                      className="flex-1"
                    >
                      <X className="mr-2 h-4 w-4" /> 반려
                    </Button>
                  </div>
                )}

                {(selected.status === "provisioning" ||
                  selected.status === "building") && (
                  <p className="rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                    빌드가 진행 중입니다. 완료되면 테스트·공개 버튼이 나타납니다.
                  </p>
                )}

                {selected.status === "built" && (
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      onClick={() => testRun.mutate()}
                      disabled={testRun.isPending || publish.isPending}
                      className="flex-1"
                    >
                      테스트 실행
                    </Button>
                    <Button
                      onClick={() => publish.mutate()}
                      disabled={publish.isPending || testRun.isPending}
                      className="flex-1"
                    >
                      <Check className="mr-2 h-4 w-4" /> 공개
                    </Button>
                  </div>
                )}

                {selected.status === "failed" && (
                  <Button
                    onClick={() => retry.mutate()}
                    disabled={retry.isPending}
                    className="w-full"
                  >
                    재시도
                  </Button>
                )}

                {selected.status === "published" && (
                  <p className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm">
                    공개됨 — 카탈로그와 <code>/apps/{selected.proposed_app_id}/</code> 에서 확인.
                  </p>
                )}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
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
