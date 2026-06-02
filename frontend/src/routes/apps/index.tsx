import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { AppFilters } from "@/components/apps/AppFilters";
import { AppGrid } from "@/components/apps/AppGrid";
import { Button } from "@/components/ui/button";
import { appsApi, type AppListQuery } from "@/lib/api/apps";

export const Route = createFileRoute("/apps/")({
  component: AppsCatalogPage,
  validateSearch: (s): { q?: string } => ({
    q: typeof s.q === "string" ? s.q : undefined,
  }),
});

function AppsCatalogPage() {
  const initial = Route.useSearch();
  const [filters, setFilters] = useState<AppListQuery>({ q: initial.q });

  const { data, isLoading } = useQuery({
    queryKey: ["apps", filters],
    queryFn: () => appsApi.list(filters),
  });

  const total = data?.total ?? 0;
  const page = filters.page ?? 1;
  const pageSize = filters.page_size ?? 24;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mx-auto max-w-7xl px-6 py-8 md:px-10">
      <header className="mb-8">
        <h1 className="text-3xl font-bold tracking-tight">앱 카탈로그</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          현재 {total.toLocaleString()}개의 자동화 앱이 등록되어 있습니다.
        </p>
      </header>

      <div className="grid gap-8 lg:grid-cols-[240px_1fr]">
        <AppFilters value={filters} onChange={setFilters} />
        <div className="space-y-6">
          <AppGrid apps={data?.items} isLoading={isLoading} />
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
