import { createFileRoute } from "@tanstack/react-router";
import { ServiceInstanceList } from "@/components/admin/ServiceInstanceCard";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/services")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminServicesPage />
    </RequireAuth>
  ),
});

function AdminServicesPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">서비스 인스턴스</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        `launch.mode: service` 로 등록된 장기 실행 데몬. 헬스체크 결과와 재시작 횟수를 모니터링합니다.
      </p>
      <div className="mt-4 rounded-md border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-100">
        외부 접근 경로:{" "}
        <code className="font-mono">http://localhost:4180/apps/{"{app_id}"}/</code>
        {" "}— Caddy(heax-caddy) 가 `/apps/{"{app_id}"}` 접두어를 strip 한 뒤 할당된 포트로 포워딩합니다.
      </div>
      <div className="mt-6">
        <ServiceInstanceList />
      </div>
    </div>
  );
}
