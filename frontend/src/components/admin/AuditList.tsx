import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { adminApi } from "@/lib/api/admin";
import { formatDateTime } from "@/lib/utils/format";

export function AuditList() {
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "audit"],
    queryFn: () => adminApi.audit(),
  });
  if (isLoading) return <Skeleton className="h-96 w-full" />;
  const items = data?.items ?? [];

  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>시각</TableHead>
            <TableHead>수행자</TableHead>
            <TableHead>액션</TableHead>
            <TableHead>대상</TableHead>
            <TableHead>IP</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="font-mono text-xs">{formatDateTime(e.created_at)}</TableCell>
              <TableCell className="text-sm">{e.actor_display ?? "system"}</TableCell>
              <TableCell>
                <code className="text-xs">{e.action}</code>
              </TableCell>
              <TableCell className="text-sm">
                {e.target_type} <span className="text-muted-foreground">/</span>{" "}
                <code className="text-xs">{e.target_id}</code>
              </TableCell>
              <TableCell className="font-mono text-xs text-muted-foreground">
                {e.ip_address ?? "—"}
              </TableCell>
            </TableRow>
          ))}
          {items.length === 0 && (
            <TableRow>
              <TableCell colSpan={5} className="py-12 text-center text-sm text-muted-foreground">
                감사 로그가 없습니다.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
