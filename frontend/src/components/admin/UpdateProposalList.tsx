import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, GitBranch, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { adminApi } from "@/lib/api/admin";
import { formatDateTime } from "@/lib/utils/format";

export function UpdateProposalList() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "updates"],
    queryFn: () => adminApi.updates(),
  });

  const approve = useMutation({
    mutationFn: (id: string) => adminApi.approveUpdate(id),
    onSuccess: () => {
      toast.success("업데이트가 빌드 큐에 적재되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "updates"] });
    },
  });
  const ignore = useMutation({
    mutationFn: (id: string) => adminApi.ignoreUpdate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "updates"] }),
  });

  if (isLoading) return <Skeleton className="h-64 w-full" />;

  const items = data ?? [];
  if (items.length === 0) {
    return (
      <div className="rounded-lg border border-dashed bg-card/40 py-12 text-center text-sm text-muted-foreground">
        검토 대기 중인 업데이트가 없습니다.
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      {items.map((u) => (
        <Card key={u.id}>
          <CardContent className="flex items-center justify-between gap-4 py-4">
            <div className="flex items-start gap-3">
              <GitBranch className="mt-0.5 h-5 w-5 text-emerald-500" />
              <div>
                <div className="text-sm font-semibold">{u.app_name}</div>
                <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                  {u.current_commit?.slice(0, 7) ?? "—"} → {u.latest_commit?.slice(0, 7) ?? "—"}
                  {u.latest_tag && (
                    <span className="ml-2 rounded bg-secondary px-1.5 py-0.5 text-[10px]">
                      {u.latest_tag}
                    </span>
                  )}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  감지 {formatDateTime(u.detected_at)}
                </div>
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => ignore.mutate(u.id)}
                disabled={ignore.isPending || approve.isPending}
              >
                <X className="mr-1.5 h-4 w-4" /> 무시
              </Button>
              <Button
                size="sm"
                onClick={() => approve.mutate(u.id)}
                disabled={ignore.isPending || approve.isPending}
              >
                <Check className="mr-1.5 h-4 w-4" /> 빌드 승인
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
