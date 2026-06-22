import { Link, useRouterState } from "@tanstack/react-router";
import {
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Compass,
  Cpu,
  Github,
  History,
  Home,
  KeyRound,
  Laptop,
  Package,
  Send,
  Server,
  Shield,
  Ticket,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/lib/auth/useAuth";
import { cn } from "@/lib/utils/cn";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  roles?: string[];
}

const items: NavItem[] = [
  { to: "/", label: "홈", icon: Home },
  { to: "/apps", label: "앱 카탈로그", icon: Compass },
  { to: "/jobs", label: "내 실행", icon: History },
  { to: "/submit", label: "새 앱 신청", icon: Send },
  { to: "/submit/my", label: "내 신청", icon: ClipboardList },
];

const adminItems: NavItem[] = [
  { to: "/admin", label: "대시보드", icon: Shield, roles: ["admin"] },
  { to: "/admin/submissions", label: "신청 큐", icon: ClipboardList, roles: ["admin"] },
  { to: "/admin/change-requests", label: "변경 요청", icon: Github, roles: ["admin"] },
  { to: "/admin/updates", label: "업스트림 갱신", icon: History, roles: ["admin"] },
  { to: "/admin/secrets", label: "시크릿", icon: KeyRound, roles: ["admin"] },
  { to: "/admin/licenses", label: "라이선스", icon: Ticket, roles: ["admin"] },
  { to: "/admin/gpus", label: "GPU", icon: Cpu, roles: ["admin"] },
  { to: "/admin/services", label: "서비스", icon: Server, roles: ["admin"] },
  { to: "/admin/agents", label: "Windows Agent", icon: Laptop, roles: ["admin"] },
  { to: "/admin/installers", label: "설치 파일", icon: Package, roles: ["admin"] },
  { to: "/admin/integrations", label: "GitHub 통합", icon: Github, roles: ["admin"] },
  { to: "/admin/users", label: "사용자", icon: Compass, roles: ["admin"] },
  { to: "/admin/audit", label: "감사 로그", icon: ClipboardList, roles: ["admin"] },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem("heaxhub.sidebar.collapsed") === "1";
  });
  const { hasRole } = useAuth();
  const { location } = useRouterState();

  useEffect(() => {
    localStorage.setItem("heaxhub.sidebar.collapsed", collapsed ? "1" : "0");
  }, [collapsed]);

  return (
    <aside
      className={cn(
        "sticky top-14 hidden h-[calc(100vh-3.5rem)] shrink-0 flex-col border-r bg-card/40 transition-all md:flex",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <nav className="flex-1 space-y-1 px-2 py-4">
        {items.map((it) => (
          <NavLink key={it.to} item={it} collapsed={collapsed} active={isActive(location.pathname, it.to)} />
        ))}

        {hasRole("admin") && (
          <>
            <Separator className="my-3" />
            <div
              className={cn(
                "px-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground",
                collapsed && "sr-only",
              )}
            >
              관리자
            </div>
            {adminItems.map((it) => (
              <NavLink
                key={it.to}
                item={it}
                collapsed={collapsed}
                active={isActive(location.pathname, it.to)}
              />
            ))}
          </>
        )}
      </nav>

      <button
        onClick={() => setCollapsed((c) => !c)}
        className="flex items-center justify-center gap-2 border-t py-2 text-xs text-muted-foreground hover:bg-accent"
        type="button"
        aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
      >
        {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        {!collapsed && <span>접기</span>}
      </button>
    </aside>
  );
}

function isActive(pathname: string, to: string) {
  if (to === "/") return pathname === "/";
  return pathname === to || pathname.startsWith(`${to}/`);
}

function NavLink({
  item,
  collapsed,
  active,
}: {
  item: NavItem;
  collapsed: boolean;
  active: boolean;
}) {
  const Icon = item.icon;
  return (
    <Link
      to={item.to as never}
      className={cn(
        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
        active && "bg-accent text-foreground",
        collapsed && "justify-center px-0",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span>{item.label}</span>}
    </Link>
  );
}
