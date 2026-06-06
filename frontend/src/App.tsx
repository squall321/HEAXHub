import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { useEffect } from "react";
import { Toaster } from "sonner";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { ThemeProvider, useTheme } from "@/components/common/ThemeProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useAuth } from "@/lib/auth/useAuth";
import { useAuthStore } from "@/lib/auth/store";
import { routeTree } from "./routeTree.gen";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const router = createRouter({
  routeTree,
  // Behind the HWAX portal the app is served under a sub-path; BASE_URL is "/" standalone or
  // "/heax-hub/" there. TanStack wants the basepath WITHOUT the trailing slash.
  basepath: import.meta.env.BASE_URL.replace(/\/$/, "") || "/",
  defaultPreload: "intent",
  context: { queryClient },
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

function AuthSync() {
  const { refreshMe } = useAuth();
  const token = useAuthStore((s) => s.accessToken);
  useEffect(() => {
    if (token) {
      refreshMe();
    }
    // We only run on mount to hydrate user. Subsequent token rotations are handled in client.ts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return null;
}

function ToasterWithTheme() {
  const { resolved } = useTheme();
  return <Toaster position="top-right" richColors theme={resolved} />;
}

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider>
          <TooltipProvider delayDuration={200}>
            <AuthSync />
            <RouterProvider router={router} />
            <ToasterWithTheme />
          </TooltipProvider>
        </ThemeProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
