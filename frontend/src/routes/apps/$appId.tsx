import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { ExternalLink, GitBranch, History, Play, Star, User } from "lucide-react";
import { ManifestPreview } from "@/components/apps/ManifestPreview";
import { RunForm } from "@/components/apps/RunForm";
import { StatusBadge } from "@/components/apps/StatusBadge";
import { JobTable } from "@/components/jobs/JobTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { appsApi } from "@/lib/api/apps";
import { jobsApi } from "@/lib/api/jobs";
import { formatDateTime } from "@/lib/utils/format";
import { categoryLabel, colors } from "@/styles/tokens";

export const Route = createFileRoute("/apps/$appId")({
  component: AppDetailPage,
});

function AppDetailPage() {
  const { appId } = Route.useParams();

  const { data: app, isLoading } = useQuery({
    queryKey: ["apps", appId],
    queryFn: () => appsApi.detail(appId),
  });

  const history = useQuery({
    queryKey: ["apps", appId, "jobs"],
    queryFn: () => jobsApi.list({ app_id: appId, page_size: 10 }),
    enabled: Boolean(app),
  });

  if (isLoading || !app) {
    return (
      <div className="mx-auto max-w-6xl px-6 py-8">
        <Skeleton className="h-40 w-full" />
        <Skeleton className="mt-6 h-96 w-full" />
      </div>
    );
  }

  const accent = colors.category[app.app_type];

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 md:px-10">
      {/* HEADER */}
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="overflow-hidden rounded-2xl border bg-card shadow-sm"
        style={{ borderTop: `4px solid ${accent}` }}
      >
        <div className="p-6 md:p-8">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="rounded-full px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider"
              style={{ background: `${accent}1f`, color: accent }}
            >
              {categoryLabel[app.app_type]}
            </span>
            <StatusBadge status={app.status} />
            {app.current_version && <Badge variant="muted">v{app.current_version}</Badge>}
            <Badge variant="outline">{app.visibility}</Badge>
          </div>
          <h1 className="mt-3 text-3xl font-bold tracking-tight">{app.name}</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{app.id}</p>
          {app.description && (
            <p className="mt-4 max-w-3xl text-sm leading-relaxed text-muted-foreground">
              {app.description}
            </p>
          )}
          <div className="mt-5 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <User className="h-3.5 w-3.5" /> {app.owner_display ?? app.owner_user_id}
            </span>
            {app.upstream_repo_url && (
              <a
                href={app.upstream_repo_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 hover:text-foreground"
              >
                <GitBranch className="h-3.5 w-3.5" /> 업스트림
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
            <span>업데이트 {formatDateTime(app.updated_at)}</span>
          </div>
          <div className="mt-6 flex gap-2">
            <Button asChild>
              <Link to="/apps/$appId/run" params={{ appId }}>
                <Play className="mr-1.5 h-4 w-4" /> 실행
              </Link>
            </Button>
            <Button variant="outline" size="default">
              <Star className="mr-1.5 h-4 w-4" /> 즐겨찾기
            </Button>
          </div>
        </div>
      </motion.div>

      {/* TABS */}
      <Tabs defaultValue="usage" className="mt-8">
        <TabsList>
          <TabsTrigger value="usage">사용법</TabsTrigger>
          <TabsTrigger value="run">실행</TabsTrigger>
          <TabsTrigger value="history">이력</TabsTrigger>
          <TabsTrigger value="versions">변경 이력</TabsTrigger>
          <TabsTrigger value="docs">문서</TabsTrigger>
        </TabsList>

        <TabsContent value="usage" className="space-y-4">
          {app.manifest ? (
            <ManifestPreview manifest={app.manifest} />
          ) : (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                manifest 정보가 없습니다.
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="run">
          {app.manifest ? (
            <RunForm appId={appId} manifest={app.manifest} />
          ) : (
            <Card>
              <CardContent className="py-10 text-center text-sm text-muted-foreground">
                실행 가능한 manifest가 아직 준비되지 않았습니다.
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="history">
          <div className="flex items-center gap-2 pb-3 text-sm text-muted-foreground">
            <History className="h-4 w-4" />
            최근 10건
          </div>
          <JobTable
            jobs={history.data?.items}
            isLoading={history.isLoading}
            showApp={false}
          />
        </TabsContent>

        <TabsContent value="versions">
          <Card>
            <CardHeader>
              <CardTitle>버전 이력</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {app.versions.length === 0 ? (
                <p className="text-sm text-muted-foreground">버전 정보가 없습니다.</p>
              ) : (
                app.versions.map((v) => (
                  <div
                    key={v.id}
                    className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2"
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant="muted">v{v.version}</Badge>
                      <code className="text-xs text-muted-foreground">
                        {v.git_commit_hash?.slice(0, 7) ?? "—"}
                      </code>
                      {v.git_tag && <Badge variant="outline">{v.git_tag}</Badge>}
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <Badge
                        variant={v.build_status === "success" ? "success" : v.build_status === "failed" ? "destructive" : "warning"}
                      >
                        {v.build_status}
                      </Badge>
                      <span>{formatDateTime(v.released_at)}</span>
                    </div>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="docs">
          <Card>
            <CardContent className="py-8">
              {app.readme ? (
                <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">
                  {app.readme}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">문서가 등록되지 않았습니다.</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
