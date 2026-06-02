import { useQuery } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { Activity, Boxes, ClipboardList, Database, Send, Users } from "lucide-react";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { adminApi } from "@/lib/api/admin";

export const Route = createFileRoute("/admin/")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminDashboard />
    </RequireAuth>
  ),
});

function AdminDashboard() {
  const stats = useQuery({ queryKey: ["admin", "stats"], queryFn: () => adminApi.stats() });
  const health = useQuery({
    queryKey: ["admin", "health"],
    queryFn: () => adminApi.systemHealth(),
    refetchInterval: 10_000,
  });

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-6 py-8 md:px-10">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">관리자 대시보드</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          신청 검토와 시스템 상태를 한눈에 확인하세요.
        </p>
      </header>

      {/* Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="오늘 실행"
          value={stats.data?.jobs_today}
          icon={<Activity className="h-5 w-5 text-primary" />}
          loading={stats.isLoading}
        />
        <Stat
          label="활성 사용자"
          value={stats.data?.active_users_today}
          icon={<Users className="h-5 w-5 text-emerald-500" />}
          loading={stats.isLoading}
        />
        <Stat
          label="빌드 큐"
          value={stats.data?.build_queue_depth}
          icon={<Boxes className="h-5 w-5 text-amber-500" />}
          loading={stats.isLoading}
        />
        <Stat
          label="신청 대기"
          value={stats.data?.pending_submissions}
          icon={<Send className="h-5 w-5 text-pink-500" />}
          loading={stats.isLoading}
        />
      </div>

      {/* Health */}
      <Card>
        <CardContent className="grid gap-4 p-6 sm:grid-cols-3">
          <HealthRow
            label="API"
            ok={health.data?.status === "ok"}
            note={health.data?.status ?? "—"}
            icon={<Activity className="h-4 w-4" />}
          />
          <HealthRow
            label="Database"
            ok={Boolean(health.data?.db_ok)}
            icon={<Database className="h-4 w-4" />}
          />
          <HealthRow
            label="Redis"
            ok={Boolean(health.data?.redis_ok)}
            icon={<Boxes className="h-4 w-4" />}
          />
        </CardContent>
      </Card>

      {/* Quick Links */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <QuickLink to="/admin/submissions" icon={<ClipboardList />} label="신청 큐" />
        <QuickLink to="/admin/updates" icon={<Boxes />} label="업스트림 갱신" />
        <QuickLink to="/admin/users" icon={<Users />} label="사용자 관리" />
        <QuickLink to="/admin/audit" icon={<Activity />} label="감사 로그" />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  icon,
  loading,
}: {
  label: string;
  value?: number;
  icon: React.ReactNode;
  loading: boolean;
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-5">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {label}
          </div>
          {loading ? (
            <Skeleton className="mt-2 h-7 w-12" />
          ) : (
            <div className="mt-1 text-2xl font-bold">{(value ?? 0).toLocaleString()}</div>
          )}
        </div>
        <div className="rounded-lg bg-muted p-2">{icon}</div>
      </CardContent>
    </Card>
  );
}

function HealthRow({
  label,
  ok,
  note,
  icon,
}: {
  label: string;
  ok: boolean;
  note?: string;
  icon: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border bg-muted/30 px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        {icon}
        {label}
      </div>
      <Badge variant={ok ? "success" : "destructive"}>{ok ? "OK" : note ?? "DOWN"}</Badge>
    </div>
  );
}

function QuickLink({
  to,
  icon,
  label,
}: {
  to: string;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <Link
      to={to as never}
      className="group flex items-center gap-3 rounded-xl border bg-card p-4 transition-colors hover:border-primary/40 hover:bg-accent"
    >
      <div className="rounded-lg bg-primary/10 p-2 text-primary group-hover:bg-primary/20">
        {icon}
      </div>
      <span className="text-sm font-semibold">{label}</span>
    </Link>
  );
}
