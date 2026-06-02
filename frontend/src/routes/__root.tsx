import { Outlet, createRootRoute } from "@tanstack/react-router";
import { AppShell } from "@/components/layout/AppShell";

export const Route = createRootRoute({
  component: RootLayout,
  notFoundComponent: NotFound,
});

function RootLayout() {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}

function NotFound() {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center text-center">
      <div className="text-6xl font-black text-muted-foreground">404</div>
      <h2 className="mt-4 text-xl font-semibold">페이지를 찾을 수 없습니다.</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        주소를 다시 확인하거나 사이드바에서 이동해 주세요.
      </p>
    </div>
  );
}
