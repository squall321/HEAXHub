import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { ChevronLeft } from "lucide-react";
import { RunForm } from "@/components/apps/RunForm";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { appsApi } from "@/lib/api/apps";

export const Route = createFileRoute("/apps/$appId/run")({
  component: RunPage,
});

function RunPage() {
  const { appId } = Route.useParams();
  const { data: app, isLoading } = useQuery({
    queryKey: ["apps", appId],
    queryFn: () => appsApi.detail(appId),
  });

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 md:px-10">
      <Link to="/apps/$appId" params={{ appId }}>
        <Button variant="ghost" size="sm" className="mb-4 -ml-2">
          <ChevronLeft className="mr-1 h-4 w-4" /> {app?.name ?? "앱 상세"}
        </Button>
      </Link>

      <h1 className="text-2xl font-bold tracking-tight">실행</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        manifest에 정의된 입력을 채워 작업을 큐에 적재합니다.
      </p>

      <div className="mt-6">
        {isLoading || !app ? (
          <Skeleton className="h-96 w-full" />
        ) : app.manifest ? (
          <RunForm appId={appId} manifest={app.manifest} />
        ) : (
          <div className="rounded-lg border border-dashed bg-card/40 py-12 text-center text-sm text-muted-foreground">
            실행 가능한 manifest가 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}
