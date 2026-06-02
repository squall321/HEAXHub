import { createFileRoute } from "@tanstack/react-router";
import { UpdateProposalList } from "@/components/admin/UpdateProposalList";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/updates")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminUpdatesPage />
    </RequireAuth>
  ),
});

function AdminUpdatesPage() {
  return (
    <div className="mx-auto max-w-5xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">업스트림 갱신</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        upstream 저장소에서 새 커밋·태그가 감지된 항목입니다. 승인 시 빌드 큐에 적재됩니다.
      </p>
      <div className="mt-6">
        <UpdateProposalList />
      </div>
    </div>
  );
}
