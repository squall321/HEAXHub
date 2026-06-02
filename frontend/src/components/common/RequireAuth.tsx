import { Navigate, useLocation } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useAuth } from "@/lib/auth/useAuth";
import type { UserRole } from "@/lib/api/types";

interface RequireAuthProps {
  children: ReactNode;
  roles?: UserRole[];
}

export function RequireAuth({ children, roles }: RequireAuthProps) {
  const { user, isLoggedIn } = useAuth();
  const location = useLocation();

  if (!isLoggedIn) {
    return <Navigate to="/login" search={{ next: location.pathname }} />;
  }

  if (roles && roles.length > 0 && (!user || !roles.includes(user.role))) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
        <h2 className="text-xl font-semibold">접근 권한이 없습니다</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          이 페이지는 {roles.join(", ")} 권한이 필요합니다.
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
