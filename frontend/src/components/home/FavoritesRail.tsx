import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, RotateCw, Star } from "lucide-react";
import { AppCard } from "@/components/apps/AppCard";
import { Skeleton } from "@/components/ui/skeleton";
import { appsApi } from "@/lib/api/apps";
import { useAuth } from "@/lib/auth/useAuth";
import { cn } from "@/lib/utils/cn";
import { Rail } from "./Rail";

/**
 * 즐겨찾기 rail — compact AppCards of the user's starred apps.
 * Hidden when not logged in or empty.
 */
export function FavoritesRail() {
  const { isLoggedIn } = useAuth();
  const favorites = useQuery({
    queryKey: ["apps", "favorites"],
    queryFn: () => appsApi.favorites(),
    enabled: isLoggedIn,
  });

  if (!isLoggedIn) return null;

  if (favorites.isLoading) {
    return (
      <Rail
        title="즐겨찾기"
        icon={<Star className="h-3.5 w-3.5" />}
        cta={{ to: "/apps", label: "전체 보기" }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-36 w-[280px] shrink-0 sm:w-[320px]" />
        ))}
      </Rail>
    );
  }

  if (favorites.isError) {
    return (
      <Rail title="즐겨찾기" icon={<Star className="h-3.5 w-3.5" />}>
        <div
          role="alert"
          className={cn(
            "flex w-full items-center gap-3 rounded-xl border border-rose-500/30",
            "bg-rose-500/5 px-4 py-3 text-sm text-rose-200",
          )}
        >
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="flex-1">즐겨찾기를 불러오지 못했습니다.</span>
          <button
            type="button"
            onClick={() => favorites.refetch()}
            className={cn(
              "inline-flex items-center gap-1 rounded-md border border-rose-400/40",
              "px-2 py-1 text-xs font-semibold",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/40 focus-visible:ring-offset-2 focus-visible:ring-offset-background",
            )}
            aria-label="즐겨찾기 다시 불러오기"
          >
            <RotateCw className="h-3 w-3" /> 재시도
          </button>
        </div>
      </Rail>
    );
  }

  const apps = favorites.data ?? [];
  if (apps.length === 0) return null;

  return (
    <Rail
      title="즐겨찾기"
      icon={<Star className="h-3.5 w-3.5" />}
      cta={{ to: "/apps", label: "전체 보기" }}
    >
      {apps.slice(0, 8).map((app) => (
        <div
          key={app.id}
          className="w-[280px] shrink-0 snap-start sm:w-[320px]"
        >
          <AppCard app={app} compact />
        </div>
      ))}
    </Rail>
  );
}
