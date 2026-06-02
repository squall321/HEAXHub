import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute, useNavigate } from "@tanstack/react-router";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Compass, History, Search, Sparkles, Star, Zap } from "lucide-react";
import { useState } from "react";
import { AppCard } from "@/components/apps/AppCard";
import { BrandLogo } from "@/components/common/BrandLogo";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { adminApi } from "@/lib/api/admin";
import { appsApi } from "@/lib/api/apps";
import { jobsApi } from "@/lib/api/jobs";
import { useAuth } from "@/lib/auth/useAuth";
import { timeAgo } from "@/lib/utils/format";
import { categoryLabel, colors } from "@/styles/tokens";
import type { AppType } from "@/lib/api/types";

export const Route = createFileRoute("/")({
  component: HomePage,
});

const CATEGORY_CHIPS: AppType[] = [
  "cli_tool",
  "web_app",
  "windows_gui",
  "slurm_job",
  "container_app",
  "remote_app",
  "external_link",
];

function HomePage() {
  const { user, isLoggedIn, hasRole } = useAuth();
  const [activeCategory, setActiveCategory] = useState<AppType | "all">("all");
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const recommended = useQuery({
    queryKey: ["apps", "recommended"],
    queryFn: () => appsApi.recommended(),
  });

  const favorites = useQuery({
    queryKey: ["apps", "favorites"],
    queryFn: () => appsApi.favorites(),
    enabled: isLoggedIn,
  });

  const recentJobs = useQuery({
    queryKey: ["jobs", "recent"],
    queryFn: () => jobsApi.list({ mine: true, page: 1, page_size: 5 }),
    enabled: isLoggedIn,
  });

  const adminStats = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: () => adminApi.stats(),
    enabled: hasRole("admin"),
  });

  const filtered =
    activeCategory === "all"
      ? recommended.data
      : recommended.data?.filter((a) => a.app_type === activeCategory);

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    navigate({ to: "/apps", search: { q: query } as never });
  };

  return (
    <div className="space-y-10 pb-16">
      {/* HERO */}
      <section
        className="relative isolate overflow-hidden bg-hero-radial"
        style={{
          background: "linear-gradient(140deg,#020617 0%,#1e1b4b 40%,#4338ca 100%)",
        }}
      >
        <div className="relative z-10 px-6 py-16 md:px-12 md:py-24 lg:px-16">
          <motion.div
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, ease: "easeOut" }}
            className="max-w-3xl"
          >
            <div className="mb-4 flex items-center gap-3 text-[11px] font-bold uppercase tracking-[0.25em] text-amber-300">
              <span className="block h-px w-7 bg-amber-300" />
              AI Automation Portal
            </div>
            <BrandLogo size="lg" className="mb-5 block" />
            <h1 className="text-3xl font-black leading-[1.05] tracking-tight text-white md:text-4xl lg:text-5xl">
              흩어진 자동화를{" "}
              <span className="text-amber-300">한 곳에서</span> 검색하고 실행하세요
            </h1>
            <p className="mt-5 max-w-2xl text-base text-white/75 md:text-lg">
              사내 CAE, 데이터 분석, 윈도우 GUI까지 — 신청·승인·빌드·공개가 자동화된 하나의 포탈에서.
            </p>

            <form onSubmit={submitSearch} className="mt-8 flex max-w-2xl items-center gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-white/50" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="앱 이름, 태그, 설명으로 검색"
                  className="h-14 w-full rounded-xl border border-white/15 bg-white/[0.07] pl-12 pr-4 text-base text-white placeholder:text-white/40 backdrop-blur-md transition-colors focus:border-amber-300/60 focus:bg-white/10 focus:outline-none"
                />
              </div>
              <Button
                type="submit"
                size="lg"
                variant="gold"
                className="h-14 px-7 text-base font-bold shadow-lg"
              >
                검색 <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
            </form>

            <div className="mt-6 flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-white/50">
                추천 카테고리
              </span>
              {CATEGORY_CHIPS.map((cat) => {
                const c = colors.category[cat];
                const active = activeCategory === cat;
                return (
                  <button
                    key={cat}
                    type="button"
                    onClick={() => setActiveCategory(active ? "all" : cat)}
                    className="rounded-full border px-3 py-1 text-xs font-bold uppercase tracking-wider transition-colors"
                    style={{
                      background: active ? c : "rgba(255,255,255,0.06)",
                      borderColor: active ? c : "rgba(255,255,255,0.18)",
                      color: active ? "#0b1020" : "rgba(255,255,255,0.85)",
                    }}
                  >
                    {categoryLabel[cat]}
                  </button>
                );
              })}
            </div>

            {hasRole("admin") && adminStats.data && (
              <div className="mt-10 grid max-w-2xl grid-cols-2 gap-3 md:grid-cols-4">
                <KPI label="오늘 실행" value={adminStats.data.jobs_today} />
                <KPI label="활성 사용자" value={adminStats.data.active_users_today} />
                <KPI label="빌드 큐" value={adminStats.data.build_queue_depth} />
                <KPI label="신청 대기" value={adminStats.data.pending_submissions} />
              </div>
            )}
          </motion.div>
        </div>
      </section>

      {/* QUICK START + RECENT */}
      <section className="mx-auto w-full max-w-7xl space-y-6 px-6 md:px-10">
        {isLoggedIn && user && (
          <div className="grid gap-4 lg:grid-cols-2">
            <Card className="border-l-4" style={{ borderLeftColor: colors.accent.gold }}>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Star className="h-4 w-4 text-amber-500" />
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                      즐겨찾기
                    </h2>
                  </div>
                  <Link
                    to="/apps"
                    className="text-xs text-muted-foreground transition-colors hover:text-foreground"
                  >
                    전체 보기
                  </Link>
                </div>
                <div className="mt-3 space-y-1.5">
                  {favorites.isLoading ? (
                    <Skeleton className="h-24 w-full" />
                  ) : favorites.data && favorites.data.length > 0 ? (
                    favorites.data.slice(0, 4).map((app) => (
                      <Link
                        key={app.id}
                        to="/apps/$appId"
                        params={{ appId: app.id }}
                        className="flex items-center justify-between rounded-md px-3 py-2 transition-colors hover:bg-accent"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            className="h-2 w-2 rounded-full"
                            style={{ background: colors.category[app.app_type] }}
                          />
                          <span className="text-sm font-medium">{app.name}</span>
                        </div>
                        <Badge variant="muted" className="text-[10px]">
                          {categoryLabel[app.app_type]}
                        </Badge>
                      </Link>
                    ))
                  ) : (
                    <p className="py-4 text-sm text-muted-foreground">
                      즐겨찾기한 앱이 아직 없습니다.
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardContent className="p-6">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <History className="h-4 w-4 text-primary" />
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                      내 최근 실행
                    </h2>
                  </div>
                  <Link
                    to="/jobs"
                    className="text-xs text-muted-foreground transition-colors hover:text-foreground"
                  >
                    전체 이력
                  </Link>
                </div>
                <div className="mt-3 space-y-1.5">
                  {recentJobs.isLoading ? (
                    <Skeleton className="h-24 w-full" />
                  ) : recentJobs.data && recentJobs.data.items.length > 0 ? (
                    recentJobs.data.items.map((j) => (
                      <Link
                        key={j.id}
                        to="/jobs/$jobId"
                        params={{ jobId: j.id }}
                        className="flex items-center justify-between rounded-md px-3 py-2 transition-colors hover:bg-accent"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <JobStatusBadge status={j.status} />
                          <span className="truncate text-sm font-medium">
                            {j.app_name ?? j.app_id}
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {timeAgo(j.started_at ?? j.created_at)}
                        </span>
                      </Link>
                    ))
                  ) : (
                    <p className="py-4 text-sm text-muted-foreground">
                      실행 이력이 없습니다. 카탈로그에서 앱을 실행해 보세요.
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </section>

      {/* BROWSE */}
      <section className="mx-auto w-full max-w-7xl px-6 md:px-10">
        <div className="mb-5 flex items-end justify-between">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <Compass className="h-3.5 w-3.5" />
              둘러보기
            </div>
            <h2 className="mt-1 text-2xl font-bold tracking-tight">추천 앱</h2>
          </div>
          <Link to="/apps">
            <Button variant="outline" size="sm">
              전체 카탈로그 <ArrowRight className="ml-1 h-3.5 w-3.5" />
            </Button>
          </Link>
        </div>

        <AnimatePresence mode="popLayout">
          {recommended.isLoading ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-44 w-full" />
              ))}
            </div>
          ) : filtered && filtered.length > 0 ? (
            <motion.div
              key={activeCategory}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
            >
              {filtered.map((app) => (
                <AppCard key={app.id} app={app} />
              ))}
            </motion.div>
          ) : (
            <div className="rounded-xl border border-dashed bg-card/40 px-6 py-16 text-center">
              <Sparkles className="mx-auto h-6 w-6 text-muted-foreground" />
              <p className="mt-3 text-sm text-muted-foreground">
                선택한 카테고리에 추천 앱이 없습니다.
              </p>
            </div>
          )}
        </AnimatePresence>
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

function KPI({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-white/15 bg-white/[0.07] px-4 py-3 backdrop-blur-md">
      <div className="text-2xl font-black text-white">{value.toLocaleString()}</div>
      <div className="mt-1 text-[10px] font-bold uppercase tracking-widest text-white/55">
        {label}
      </div>
    </div>
  );
}
