import { createFileRoute } from "@tanstack/react-router";
import { AuditList } from "@/components/admin/AuditList";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/audit")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminAuditPage />
    </RequireAuth>
  ),
});

function AdminAuditPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">감사 로그</h1>
      <p className="mt-1 text-sm text-muted-foreground">상태 변경 액션이 모두 기록됩니다.</p>
      <div className="mt-6">
        <AuditList />
      </div>
    </div>
  );
}
