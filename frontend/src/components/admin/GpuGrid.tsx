import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { gpusApi } from "@/lib/api/gpus";
import type { GpuDevice } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";

export function GpuGrid() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "gpus"],
    queryFn: () => gpusApi.list(),
    refetchInterval: 15_000,
  });

  const refresh = useMutation({
    mutationFn: () => gpusApi.refresh(),
    onSuccess: () => {
      toast.success("GPU 인벤토리가 갱신되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "gpus"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "갱신 실패"),
  });

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (error) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
        <p className="font-medium">아직 사용할 수 없습니다.</p>
        <p className="mt-1 text-muted-foreground">
          백엔드 `/admin/gpus` 엔드포인트가 준비되면 표시됩니다.
        </p>
      </div>
    );
  }

  const items = data ?? [];

  return (
    <>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          호스트의 `nvidia-smi` 결과를 기반으로 검출된 GPU 디바이스입니다.
        </p>
        <Button onClick={() => refresh.mutate()} disabled={refresh.isPending}>
          <RefreshCw className={`mr-1 h-4 w-4 ${refresh.isPending ? "animate-spin" : ""}`} />
          {refresh.isPending ? "갱신 중" : "다시 검색"}
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((g) => (
          <GpuCard key={g.id} gpu={g} />
        ))}
        {items.length === 0 && (
          <div className="col-span-full rounded-md border bg-muted/30 p-12 text-center text-sm text-muted-foreground">
            검출된 GPU가 없습니다. 호스트에 NVIDIA 드라이버가 설치되어 있는지 확인하세요.
          </div>
        )}
      </div>
    </>
  );
}

function GpuCard({ gpu }: { gpu: GpuDevice }) {
  const statusVariant =
    gpu.status === "available" ? "success" : gpu.status === "in_use" ? "info" : "muted";
  return (
    <Card>
      <CardContent className="space-y-2 p-5">
        <div className="flex items-start justify-between">
          <div>
            <div className="text-xs font-mono text-muted-foreground">GPU #{gpu.index}</div>
            <div className="text-base font-semibold">{gpu.model}</div>
          </div>
          <Badge variant={statusVariant}>{gpu.status}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Field label="VRAM">{(gpu.memory_mb / 1024).toFixed(1)} GB</Field>
          <Field label="CUDA">{gpu.cuda_version ?? "—"}</Field>
          <Field label="호스트">{gpu.host ?? "—"}</Field>
          <Field label="현재 작업">
            {gpu.current_job_id ? (
              <code className="text-[10px]">{gpu.current_job_id.slice(0, 8)}</code>
            ) : (
              "—"
            )}
          </Field>
        </div>
        <div className="border-t pt-2 text-[10px] text-muted-foreground">
          마지막 갱신 {formatDateTime(gpu.updated_at)}
        </div>
      </CardContent>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}
