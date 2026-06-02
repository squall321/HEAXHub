import { createFileRoute } from "@tanstack/react-router";
import { LicensePoolList } from "@/components/admin/LicensePoolList";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/licenses")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminLicensesPage />
    </RequireAuth>
  ),
});

function AdminLicensesPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">라이선스 풀</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        FlexLM/RLM feature 단위 토큰 큐. 점유 현황과 24시간 추세를 확인합니다.
      </p>
      <div className="mt-6">
        <LicensePoolList />
      </div>
    </div>
  );
}
