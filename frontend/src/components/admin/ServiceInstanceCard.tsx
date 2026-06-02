import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, RotateCcw, Square } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { servicesApi } from "@/lib/api/services";
import type { ServiceInstance, ServiceStatus } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";

export function ServiceInstanceList() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "services"],
    queryFn: () => servicesApi.list(),
    refetchInterval: 10_000,
  });

  const restart = useMutation({
    mutationFn: (id: string) => servicesApi.restart(id),
    onSuccess: () => {
      toast.success("재시작 요청을 보냈습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "services"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "재시작 실패"),
  });

  const stop = useMutation({
    mutationFn: (id: string) => servicesApi.stop(id),
    onSuccess: () => {
      toast.success("중지 요청을 보냈습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "services"] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "중지 실패"),
  });

  if (isLoading) return <Skeleton className="h-72 w-full" />;
  if (error) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
        <p className="font-medium">아직 사용할 수 없습니다.</p>
        <p className="mt-1 text-muted-foreground">
          백엔드 `/admin/services` 엔드포인트가 준비되면 표시됩니다.
        </p>
      </div>
    );
  }

  const items = data ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {items.map((s) => (
        <ServiceInstanceCard
          key={s.id}
          svc={s}
          onRestart={() => restart.mutate(s.id)}
          onStop={() => stop.mutate(s.id)}
          busy={restart.isPending || stop.isPending}
        />
      ))}
      {items.length === 0 && (
        <div className="col-span-full rounded-md border bg-muted/30 p-12 text-center text-sm text-muted-foreground">
          실행 중인 서비스 인스턴스가 없습니다.
        </div>
      )}
    </div>
  );
}

function statusVariant(s: ServiceStatus): "success" | "warning" | "destructive" | "muted" | "info" {
  switch (s) {
    case "healthy":
      return "success";
    case "starting":
      return "info";
    case "unhealthy":
      return "destructive";
    case "stopped":
    default:
      return "muted";
  }
}

function ServiceInstanceCard({
  svc,
  onRestart,
  onStop,
  busy,
}: {
  svc: ServiceInstance;
  onRestart: () => void;
  onStop: () => void;
  busy: boolean;
}) {
  const url =
    svc.base_path && svc.status === "healthy"
      ? svc.base_path.startsWith("http")
        ? svc.base_path
        : svc.base_path
      : null;

  return (
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-xs font-mono text-muted-foreground">{svc.app_id}</div>
            <div className="text-base font-semibold">{svc.app_name ?? svc.app_id}</div>
          </div>
          <Badge variant={statusVariant(svc.status)}>{svc.status}</Badge>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          <Field label="포트">{svc.port ?? "—"}</Field>
          <Field label="PID">{svc.pid ?? "—"}</Field>
          <Field label="재시작 횟수">{svc.restart_count}</Field>
          <Field label="시작">{formatDateTime(svc.started_at)}</Field>
          <Field label="마지막 헬스체크">
            <span className="inline-flex items-center gap-1.5">
              <HealthDot iso={svc.last_health_at} />
              {formatDateTime(svc.last_health_at)}
            </span>
          </Field>
          <Field label="경로">
            <code className="text-[10px]">{svc.base_path ?? "—"}</code>
          </Field>
        </div>

        <div className="flex items-center gap-2 border-t pt-3">
          {url && (
            <Button size="sm" variant="outline" asChild>
              <a href={url} target="_blank" rel="noreferrer">
                <ExternalLink className="mr-1 h-3.5 w-3.5" /> 열기
              </a>
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onRestart} disabled={busy}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" /> 재시작
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={onStop}
            disabled={busy || svc.status === "stopped"}
          >
            <Square className="mr-1 h-3.5 w-3.5" /> 중지
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function HealthDot({ iso }: { iso?: string | null }) {
  // 90s: green → amber. 5min: amber → red. Missing data → muted.
  if (!iso) return <span className="inline-block h-2 w-2 rounded-full bg-muted-foreground/40" />;
  let tone = "bg-emerald-500";
  try {
    const ageMs = Date.now() - new Date(iso).getTime();
    if (ageMs > 5 * 60_000) tone = "bg-red-500";
    else if (ageMs > 90_000) tone = "bg-amber-500";
  } catch {
    tone = "bg-muted-foreground/40";
  }
  return <span className={`inline-block h-2 w-2 rounded-full ${tone}`} />;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="mt-0.5">{children}</div>
    </div>
  );
}
