import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ExternalLink, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { integrationsApi } from "@/lib/api/integrations";

export const Route = createFileRoute("/admin/integrations")({
  component: () => (
    <RequireAuth roles={["admin"]}>
      <AdminIntegrationsPage />
    </RequireAuth>
  ),
});

function AdminIntegrationsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "integrations"],
    queryFn: () => integrationsApi.config(),
  });

  const testRequest = useMutation({
    mutationFn: () => integrationsApi.testRequest(),
    onSuccess: (res) => {
      toast.success("테스트 변경 요청이 생성되었습니다.");
      qc.invalidateQueries({ queryKey: ["change-requests"] });
      navigate({ to: `/admin/change-requests/${res.change_request.id}` as never });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "발행 실패"),
  });

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-6 py-8 md:px-10">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">GitHub 통합</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          데모용 통합 저장소 + LLM 설정을 확인하고 end-to-end 테스트를 실행합니다.
        </p>
      </header>

      {isLoading ? (
        <Skeleton className="h-72 w-full" />
      ) : error ? (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
          <p className="font-medium">아직 사용할 수 없습니다.</p>
          <p className="mt-1 text-muted-foreground">
            백엔드 `/admin/integrations` 엔드포인트가 준비되면 표시됩니다.
          </p>
        </div>
      ) : (
        <Card>
          <CardContent className="space-y-4 p-6">
            <Row label="Integration Repo URL">
              {data?.integration_repo_url ? (
                <a
                  href={data.integration_repo_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline"
                >
                  {data.integration_repo_url}
                  <ExternalLink className="h-3 w-3" />
                </a>
              ) : (
                <span className="text-xs text-muted-foreground">설정되지 않음</span>
              )}
            </Row>
            <Row label="Bot Username">
              {data?.github_bot_username ? (
                <code className="text-xs">{data.github_bot_username}</code>
              ) : (
                <span className="text-xs text-muted-foreground">설정되지 않음</span>
              )}
            </Row>
            <Row label="GitHub 토큰">
              <Badge variant={data?.token_configured ? "success" : "destructive"}>
                {data?.token_configured ? "configured" : "missing"}
              </Badge>
            </Row>
            <Row label="Webhook">
              <Badge variant={data?.webhook_configured ? "success" : "muted"}>
                {data?.webhook_configured ? "configured" : "not set"}
              </Badge>
            </Row>
            <Row label="LLM Provider">
              <code className="text-xs">
                {data?.llm_provider ?? "—"}
                {data?.llm_model ? ` / ${data.llm_model}` : ""}
              </code>
            </Row>

            <div className="border-t pt-4">
              <Button
                onClick={() => testRequest.mutate()}
                disabled={!data?.token_configured || !data?.integration_repo_url || testRequest.isPending}
              >
                {testRequest.isPending ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <Send className="mr-1 h-4 w-4" />
                )}
                테스트 변경 요청 발행
              </Button>
              <p className="mt-2 text-xs text-muted-foreground">
                INTEGRATION_REPO_URL 저장소로 static 분석 + LLM 호출을 즉시 실행하고 결과 화면으로 이동합니다.
              </p>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[180px_1fr] items-center gap-3 border-b py-2 last:border-0">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div>{children}</div>
    </div>
  );
}
