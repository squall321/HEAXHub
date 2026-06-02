import { createFileRoute } from "@tanstack/react-router";
import { GpuGrid } from "@/components/admin/GpuGrid";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/gpus")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminGpusPage />
    </RequireAuth>
  ),
});

function AdminGpusPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">GPU 인벤토리</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        호스트의 `nvidia-smi` 결과를 기반으로 검출된 디바이스. 작업이 점유 중인 GPU도 표시됩니다.
      </p>
      <div className="mt-6">
        <GpuGrid />
      </div>
    </div>
  );
}
