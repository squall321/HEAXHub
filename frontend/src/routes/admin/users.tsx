import { createFileRoute } from "@tanstack/react-router";
import { UserTable } from "@/components/admin/UserTable";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/users")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminUsersPage />
    </RequireAuth>
  ),
});

function AdminUsersPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">사용자 관리</h1>
      <p className="mt-1 text-sm text-muted-foreground">역할을 변경하면 즉시 적용됩니다.</p>
      <div className="mt-6">
        <UserTable />
      </div>
    </div>
  );
}
