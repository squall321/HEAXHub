import { Badge } from "@/components/ui/badge";
import type { AppStatus } from "@/lib/api/types";

const map: Record<AppStatus, { label: string; variant: "default" | "secondary" | "success" | "warning" | "muted" | "destructive" }> = {
  draft: { label: "초안", variant: "muted" },
  beta: { label: "베타", variant: "warning" },
  stable: { label: "안정", variant: "success" },
  deprecated: { label: "사용 중단 예정", variant: "warning" },
  archived: { label: "보관됨", variant: "muted" },
};

export function StatusBadge({ status }: { status: AppStatus }) {
  const entry = map[status] ?? { label: status, variant: "muted" as const };
  return <Badge variant={entry.variant}>{entry.label}</Badge>;
}
