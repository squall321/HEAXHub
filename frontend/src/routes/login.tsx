import { createFileRoute } from "@tanstack/react-router";
import { LoginForm } from "@/components/auth/LoginForm";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const Route = createFileRoute("/login")({
  component: LoginPage,
  validateSearch: (search): { next?: string } => ({
    next: typeof search.next === "string" ? search.next : undefined,
  }),
});

function LoginPage() {
  return (
    <AuthLayout>
      <Card>
        <CardHeader className="text-center">
          <CardTitle className="text-xl">다시 오신 것을 환영합니다</CardTitle>
          <CardDescription>HEAXHub 사내 자동화 포탈에 로그인하세요.</CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm />
        </CardContent>
      </Card>
    </AuthLayout>
  );
}

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-[calc(100vh-3.5rem)] overflow-hidden">
      <div
        className="absolute inset-0 -z-10 opacity-95"
        style={{
          background:
            "radial-gradient(circle at 25% 20%, rgba(67,56,202,0.12), transparent 50%), radial-gradient(circle at 75% 80%, rgba(252,211,77,0.10), transparent 55%)",
        }}
      />
      <div className="mx-auto flex max-w-md flex-col justify-center px-4 py-16">
        <div className="mb-8 text-center">
          <div
            className="mx-auto mb-3 h-12 w-12 rounded-2xl"
            style={{
              background:
                "linear-gradient(135deg,#020617 0%,#1e1b4b 50%,#4338ca 100%)",
            }}
          />
          <div className="text-xs font-semibold uppercase tracking-[0.25em] text-muted-foreground">
            HEAXHub
          </div>
        </div>
        {children}
      </div>
    </div>
  );
}
