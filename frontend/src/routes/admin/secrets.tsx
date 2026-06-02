import { createFileRoute } from "@tanstack/react-router";
import { SecretsTable } from "@/components/admin/SecretsTable";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/secrets")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminSecretsPage />
    </RequireAuth>
  ),
});

function AdminSecretsPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">시크릿</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        AES-GCM으로 암호화 저장된 환경 변수. 저장 후에는 다시 볼 수 없습니다.
      </p>
      <div className="mt-6">
        <SecretsTable />
      </div>
    </div>
  );
}
