import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
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
import { adminApi } from "@/lib/api/admin";
import type { User, UserRole } from "@/lib/api/types";
import { formatDateTime } from "@/lib/utils/format";

const ROLES: UserRole[] = ["admin", "owner", "user", "viewer"];

export function UserTable() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: () => adminApi.users(),
  });

  const updateRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: UserRole }) => adminApi.updateUserRole(id, role),
    onSuccess: () => {
      toast.success("역할이 변경되었습니다.");
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });

  if (isLoading) return <Skeleton className="h-96 w-full" />;
  const items = data?.items ?? [];

  return (
    <div className="rounded-lg border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>이름</TableHead>
            <TableHead>이메일</TableHead>
            <TableHead>조직</TableHead>
            <TableHead>역할</TableHead>
            <TableHead>상태</TableHead>
            <TableHead>마지막 로그인</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((u: User) => (
            <TableRow key={u.id}>
              <TableCell className="font-medium">{u.display_name}</TableCell>
              <TableCell className="text-sm text-muted-foreground">{u.email}</TableCell>
              <TableCell className="text-sm">{u.organization}</TableCell>
              <TableCell>
                <Select
                  value={u.role}
                  onValueChange={(role) => updateRole.mutate({ id: u.id, role: role as UserRole })}
                >
                  <SelectTrigger className="h-8 w-28">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </TableCell>
              <TableCell>
                <Badge
                  variant={
                    u.status === "active"
                      ? "success"
                      : u.status === "disabled"
                        ? "destructive"
                        : "warning"
                  }
                >
                  {u.status}
                </Badge>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatDateTime(u.last_login_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
