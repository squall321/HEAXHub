import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Zap } from "lucide-react";
import { useState } from "react";
import { CatalogGrid, type CatalogSort } from "@/components/apps/CatalogGrid";
import { AdminGlance } from "@/components/home/AdminGlance";
import { FavoritesRail } from "@/components/home/FavoritesRail";
import { HeroRibbon } from "@/components/home/HeroRibbon";
import { RecentJobsRail } from "@/components/home/RecentJobsRail";
import { StackExplorer } from "@/components/home/StackExplorer";
import { StatusStrip, type StatusTotals } from "@/components/home/StatusStrip";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { adminApi } from "@/lib/api/admin";
import { appsApi } from "@/lib/api/apps";
import { useAuth } from "@/lib/auth/useAuth";
import type { AppStatus, AppType } from "@/lib/api/types";

export const Route = createFileRoute("/")({
  component: HomePage,
});

function HomePage() {
  const { hasRole } = useAuth();
  const [catalogTypes, setCatalogTypes] = useState<AppType[]>([]);
  const [catalogStatuses, setCatalogStatuses] = useState<AppStatus[]>([]);
  const [catalogSort, setCatalogSort] = useState<CatalogSort>("recent");
  const navigate = useNavigate();

  const recommended = useQuery({
    queryKey: ["apps", "recommended"],
    queryFn: () => appsApi.recommended(),
  });

  // adminStats here drives the StatusStrip aggregate only; AdminGlance
  // owns its own copy of the same query (cached together via queryKey).
  const adminStats = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: () => adminApi.stats(),
    enabled: hasRole("admin"),
  });

  const filtered = recommended.data?.filter((a) => {
    if (catalogTypes.length > 0 && !catalogTypes.includes(a.app_type)) return false;
    if (catalogStatuses.length > 0 && !catalogStatuses.includes(a.status)) return false;
    return true;
  });

  // Build StatusStrip totals. Admin sees live counts; non-admin sees a
  // baseline derived from the recommended catalog count (still useful).
  const statusTotals: StatusTotals | null = adminStats.data
    ? {
        apps: recommended.data?.length ?? 0,
        services: adminStats.data.active_users_today,
        jobsToday: adminStats.data.jobs_today,
        gpus: adminStats.data.build_queue_depth,
      }
    : recommended.data
      ? {
          apps: recommended.data.length,
          services: 0,
          jobsToday: 0,
          gpus: 0,
        }
      : null;

  const openSearch = () => {
    navigate({ to: "/apps" });
  };

  return (
    <div className="space-y-10 pb-16">
      <HeroRibbon onSearchOpen={openSearch} isAdmin={hasRole("admin")} />
      <StatusStrip totals={statusTotals} />

      {/* FOR-YOU RAILS — each rail self-hides when empty */}
      <section className="mx-auto w-full max-w-7xl space-y-10 px-6 md:px-10">
        <RecentJobsRail />
        <FavoritesRail />
        <AdminGlance />
      </section>

      {/* STACK EXPLORER */}
      <StackExplorer />

      {/* BROWSE */}
      <section className="mx-auto w-full max-w-7xl px-6 md:px-10">
        <CatalogGrid
          apps={filtered ?? undefined}
          isLoading={recommended.isLoading}
          isError={recommended.isError}
          onRetry={() => recommended.refetch()}
          activeTypes={catalogTypes}
          onTypesChange={setCatalogTypes}
          activeStatuses={catalogStatuses}
          onStatusesChange={setCatalogStatuses}
          sort={catalogSort}
          onSortChange={setCatalogSort}
          title="추천 앱"
          catalogLinkLabel="전체 카탈로그 →"
        />
      </section>

      {/* CTA */}
      <section className="mx-auto w-full max-w-7xl px-6 md:px-10">
        <Card
          className="overflow-hidden border-0"
          style={{
            background: "linear-gradient(135deg,#1e1b4b,#4338ca)",
          }}
        >
          <CardContent className="flex flex-col items-start gap-5 p-8 md:flex-row md:items-center md:justify-between">
            <div className="text-white">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-300">
                <Zap className="h-3.5 w-3.5" /> Add Your Tool
              </div>
              <h3 className="mt-2 text-2xl font-bold">사용 중인 자동화 도구를 포탈에 등록하세요.</h3>
              <p className="mt-1 max-w-xl text-sm text-white/70">
                Git 주소만 제출하면 운영자가 검토 후 워크스페이스 생성·빌드·공개를 자동화합니다.
              </p>
            </div>
            <Link to="/submit">
              <Button variant="gold" size="lg" className="shadow-lg">
                새 앱 신청하기 <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </Link>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}

