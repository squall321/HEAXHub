import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { AppFilters } from "@/components/apps/AppFilters";
import { CatalogGrid, type CatalogSort } from "@/components/apps/CatalogGrid";
import { Button } from "@/components/ui/button";
import { appsApi, type AppListQuery } from "@/lib/api/apps";
import type { AppStatus, AppType } from "@/lib/api/types";

export const Route = createFileRoute("/apps/")({
  component: AppsCatalogPage,
  validateSearch: (s): { q?: string; stack?: string } => ({
    q: typeof s.q === "string" ? s.q : undefined,
    stack: typeof s.stack === "string" ? s.stack : undefined,
  }),
});

function AppsCatalogPage() {
  const initial = Route.useSearch();
  const [filters, setFilters] = useState<AppListQuery>({
    q: initial.q,
    stack: initial.stack,
  });
  const [catalogTypes, setCatalogTypes] = useState<AppType[]>([]);
  const [catalogStatuses, setCatalogStatuses] = useState<AppStatus[]>([]);
  const [catalogSort, setCatalogSort] = useState<CatalogSort>("recent");

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["apps", filters],
    queryFn: () => appsApi.list(filters),
  });

  const total = data?.total ?? 0;
  const page = filters.page ?? 1;
  const pageSize = filters.page_size ?? 24;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  const visibleApps = useMemo(() => {
    if (!data?.items) return undefined;
    return data.items.filter((a) => {
      if (catalogTypes.length > 0 && !catalogTypes.includes(a.app_type)) return false;
      if (catalogStatuses.length > 0 && !catalogStatuses.includes(a.status)) return false;
      return true;
    });
  }, [data?.items, catalogTypes, catalogStatuses]);

  return (
    <div className="mx-auto max-w-7xl px-6 py-10 md:px-10">
      <header className="mb-10">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          App Catalog
        </div>
        <h1 className="mt-2 text-4xl font-black tracking-tight md:text-5xl">앱 카탈로그</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          현재 {total.toLocaleString()}개의 자동화 앱이 등록되어 있습니다.
        </p>
      </header>

      <div className="grid gap-8 lg:grid-cols-[240px_1fr]">
        <AppFilters value={filters} onChange={setFilters} />
        <div className="space-y-6">
          <CatalogGrid
            apps={visibleApps}
            isLoading={isLoading}
            isError={isError}
            onRetry={() => refetch()}
            activeTypes={catalogTypes}
            onTypesChange={setCatalogTypes}
            activeStatuses={catalogStatuses}
            onStatusesChange={setCatalogStatuses}
            sort={catalogSort}
            onSortChange={setCatalogSort}
          />
          {pageCount > 1 && (
            <div className="flex items-center justify-center gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setFilters({ ...filters, page: page - 1 })}
              >
                이전
              </Button>
              <span className="px-3 text-sm text-muted-foreground">
                {page} / {pageCount}
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= pageCount}
                onClick={() => setFilters({ ...filters, page: page + 1 })}
              >
                다음
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
