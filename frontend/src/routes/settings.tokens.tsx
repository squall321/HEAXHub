// 개인 액세스 토큰(PAT) 관리 — 발급·목록·폐기. 이 토큰이 MCP 게이트웨이 연동(HEAX_MCP_TOKEN)에도 쓰인다.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Check, Copy, KeyRound, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { authApi, type PatCreated } from "@/lib/api/auth";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/settings/tokens")({
  component: TokensPage,
});

function TokensPage() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<PatCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const list = useQuery({ queryKey: ["pats"], queryFn: authApi.listTokens });

  const create = useMutation({
    mutationFn: (n: string) => authApi.createToken(n),
    onSuccess: (tok) => {
      setIssued(tok);
      setName("");
      void qc.invalidateQueries({ queryKey: ["pats"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => authApi.revokeToken(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["pats"] }),
  });

  const copy = () => {
    if (!issued) return;
    void navigator.clipboard.writeText(issued.token).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="mx-auto max-w-2xl px-4 py-8">
      <div className="mb-2 flex items-center gap-2">
        <KeyRound className="h-5 w-5" />
        <h1 className="text-xl font-bold">개인 액세스 토큰</h1>
      </div>
      <p className="mb-6 text-sm text-muted-foreground">
        API·MCP 연동에 쓰는 토큰입니다. HWAX MCP 게이트웨이 연동에는 이 토큰을{" "}
        <code className="rounded bg-muted px-1 py-0.5 text-xs">HEAX_MCP_TOKEN</code> 값으로 넣으세요.
        평문은 발급 직후 한 번만 표시됩니다.
      </p>

      {/* 발급 폼 */}
      <form
        className="mb-4 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) create.mutate(name.trim());
        }}
      >
        <input
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
          placeholder="용도 라벨 (예: claude-mcp, hwax-gateway)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={120}
        />
        <Button type="submit" disabled={!name.trim() || create.isPending}>
          {create.isPending ? "발급 중…" : "토큰 발급"}
        </Button>
      </form>

      {create.isError && (
        <p className="mb-4 text-sm text-destructive">발급 실패 — 다시 시도해 주세요.</p>
      )}

      {/* 방금 발급된 평문 — 1회 표시 */}
      {issued && (
        <div className="mb-6 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
          <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-amber-600 dark:text-amber-400">
            <TriangleAlert className="h-4 w-4" />
            지금 복사하세요 — 이 토큰은 다시 표시되지 않습니다.
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 break-all rounded bg-background px-3 py-2 font-mono text-xs">
              {issued.token}
            </code>
            <Button size="sm" variant="secondary" onClick={copy}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      )}

      {/* 목록 */}
      <h2 className="mb-2 text-sm font-semibold text-muted-foreground">발급된 토큰</h2>
      {list.isLoading ? (
        <p className="text-sm text-muted-foreground">불러오는 중…</p>
      ) : !list.data || list.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">아직 발급된 토큰이 없습니다.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {list.data.map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">{t.name}</div>
                <div className="text-xs text-muted-foreground">
                  <code>{t.token_prefix}…</code> · 발급 {t.created_at.slice(0, 10)}
                  {t.expires_at ? ` · 만료 ${t.expires_at.slice(0, 10)}` : " · 무기한"}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="text-destructive hover:text-destructive"
                disabled={revoke.isPending}
                onClick={() => {
                  if (confirm(`'${t.name}' 토큰을 폐기하시겠습니까? 즉시 무효화됩니다.`)) {
                    revoke.mutate(t.id);
                  }
                }}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
