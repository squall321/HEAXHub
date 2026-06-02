import { useMutation } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useEffect } from "react";
import { VerifyEmailNotice } from "@/components/auth/VerifyEmailNotice";
import { Card, CardContent } from "@/components/ui/card";
import { authApi } from "@/lib/api/auth";
import { AuthLayout } from "./login";

export const Route = createFileRoute("/verify-email")({
  component: VerifyEmailPage,
  validateSearch: (search): { token?: string; email?: string } => ({
    token: typeof search.token === "string" ? search.token : undefined,
    email: typeof search.email === "string" ? search.email : undefined,
  }),
});

function VerifyEmailPage() {
  const { token, email } = Route.useSearch();

  const verify = useMutation({
    mutationFn: (t: string) => authApi.verifyEmail(t),
  });

  useEffect(() => {
    if (token) verify.mutate(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  return (
    <AuthLayout>
      {token ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
            {verify.isPending ? (
              <>
                <Loader2 className="h-6 w-6 animate-spin" />
                <p className="text-sm text-muted-foreground">이메일 인증 중…</p>
              </>
            ) : verify.isError ? (
              <>
                <h2 className="text-lg font-semibold">인증 실패</h2>
                <p className="text-sm text-muted-foreground">
                  {(verify.error as Error)?.message ?? "토큰이 유효하지 않습니다."}
                </p>
              </>
            ) : verify.isSuccess ? (
              <>
                <h2 className="text-lg font-semibold">이메일이 인증되었습니다.</h2>
                <p className="text-sm text-muted-foreground">이제 로그인할 수 있습니다.</p>
              </>
            ) : null}
          </CardContent>
        </Card>
      ) : (
        <VerifyEmailNotice email={email} />
      )}
    </AuthLayout>
  );
}
