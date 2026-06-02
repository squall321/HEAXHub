import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { ChangeRequestReview } from "@/components/admin/ChangeRequestReview";
import { RequireAuth } from "@/components/common/RequireAuth";

export const Route = createFileRoute("/admin/change-requests/$crId")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <ChangeRequestDetailPage />
    </RequireAuth>
  ),
});

function ChangeRequestDetailPage() {
  const { crId } = Route.useParams();

  return (
    <div className="mx-auto max-w-[1600px] px-6 py-8 md:px-10">
      <Link
        to={"/admin/change-requests" as never}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ChevronLeft className="h-4 w-4" /> 변경 요청 목록
      </Link>
      <div className="mt-4">
        <ChangeRequestReview crId={crId} />
      </div>
    </div>
  );
}
