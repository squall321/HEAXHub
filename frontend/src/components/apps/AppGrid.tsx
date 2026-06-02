import { Skeleton } from "@/components/ui/skeleton";
import type { AppSummary } from "@/lib/api/types";
import { AppCard } from "./AppCard";

interface AppGridProps {
  apps?: AppSummary[];
  isLoading?: boolean;
  emptyMessage?: string;
}

export function AppGrid({ apps, isLoading, emptyMessage = "등록된 앱이 없습니다." }: AppGridProps) {
  if (isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-44 w-full" />
        ))}
      </div>
    );
  }

  if (!apps || apps.length === 0) {
    return (
      <div className="rounded-xl border border-dashed bg-card/40 px-6 py-16 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {apps.map((app) => (
        <AppCard key={app.id} app={app} />
      ))}
    </div>
  );
}
