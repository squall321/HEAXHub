import { createFileRoute } from "@tanstack/react-router";
import { PasswordResetForm } from "@/components/auth/PasswordResetForm";
import { PasswordResetRequestForm } from "@/components/auth/PasswordResetRequestForm";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthLayout } from "./login";

export const Route = createFileRoute("/password/reset")({
  component: PasswordResetPage,
  validateSearch: (search): { token?: string } => ({
    token: typeof search.token === "string" ? search.token : undefined,
  }),
});

function PasswordResetPage() {
  const { token } = Route.useSearch();
  return (
    <AuthLayout>
      <Card>
        <CardHeader className="text-center">
          <CardTitle className="text-xl">
            {token ? "새 비밀번호 설정" : "비밀번호 재설정"}
          </CardTitle>
          <CardDescription>
            {token
              ? "안전한 새 비밀번호를 입력하세요."
              : "이메일을 입력하면 재설정 링크를 보내드립니다."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {token ? <PasswordResetForm token={token} /> : <PasswordResetRequestForm />}
        </CardContent>
      </Card>
    </AuthLayout>
  );
}
