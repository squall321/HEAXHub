import { CheckCircle2, CircleX, Clock, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { JobStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

type Variant = "default" | "secondary" | "success" | "warning" | "muted" | "destructive" | "info";

const map: Record<
  JobStatus,
  { label: string; variant: Variant; icon: typeof CheckCircle2; spin?: boolean }
> = {
  queued: { label: "대기 중", variant: "muted", icon: Clock },
  running: { label: "실행 중", variant: "info", icon: Loader2, spin: true },
  success: { label: "성공", variant: "success", icon: CheckCircle2 },
  failed: { label: "실패", variant: "destructive", icon: XCircle },
  canceled: { label: "취소됨", variant: "muted", icon: CircleX },
};

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const entry = map[status];
  const Icon = entry.icon;
  return (
    <Badge variant={entry.variant} className="gap-1.5">
      <Icon className={cn("h-3 w-3", entry.spin && "animate-spin")} />
      {entry.label}
    </Badge>
  );
}
