import { MailCheck } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";

export function VerifyEmailNotice({ email }: { email?: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-4 py-12 text-center">
        <div className="rounded-full bg-emerald-500/10 p-4 text-emerald-500">
          <MailCheck className="h-8 w-8" />
        </div>
        <h2 className="text-xl font-semibold">이메일 인증을 진행해 주세요</h2>
        <p className="max-w-sm text-sm text-muted-foreground">
          {email ? <strong>{email}</strong> : "등록한 이메일 주소"}로 인증 메일을 발송했습니다.
          메일의 인증 링크를 클릭하면 계정이 활성화됩니다.
        </p>
        <p className="text-xs text-muted-foreground">메일이 오지 않았다면 스팸함을 확인해 주세요.</p>
      </CardContent>
    </Card>
  );
}
