import { createFileRoute } from "@tanstack/react-router";
import { SubmissionQueue } from "@/components/admin/SubmissionQueue";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/submissions")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminSubmissionsPage />
    </RequireAuth>
  ),
});

function AdminSubmissionsPage() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">신청 큐</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        신청을 클릭하면 우측 패널에서 manifest와 함께 검토할 수 있습니다.
      </p>
      <div className="mt-6">
        <SubmissionQueue />
      </div>
    </div>
  );
}
