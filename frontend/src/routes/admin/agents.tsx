import { createFileRoute } from "@tanstack/react-router";
import { AgentsTable } from "@/components/admin/AgentsTable";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/agents")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminAgentsPage />
    </RequireAuth>
  ),
});

function AdminAgentsPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">Windows Agent</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        EXE 배포 대상 PC. 등록 토큰은 발급 시 단 한 번만 표시되니 안전하게 보관하세요.
      </p>
      <div className="mt-6">
        <AgentsTable />
      </div>
    </div>
  );
}
