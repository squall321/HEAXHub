import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  ExternalLink,
  FileText,
  GitPullRequest,
  Loader2,
  MessageSquare,
  Save,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { changeRequestsApi } from "@/lib/api/changeRequests";
import type {
  ChangeRequest,
  ChangeRequestStatus,
  OpenQuestion,
  RequiredFile,
} from "@/lib/api/types";
import {
  confidenceClasses,
  statusColor,
  statusLabel,
} from "@/lib/utils/confidence";
import { formatDateTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";
import { AssistantHandoffPanel } from "./AssistantHandoffPanel";
import { ConfidenceBadge } from "./ConfidenceBadge";
import { YamlEditor } from "./YamlEditor";

interface ChangeRequestReviewProps {
  crId: string;
}

export function ChangeRequestReview({ crId }: ChangeRequestReviewProps) {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["change-requests", crId],
    queryFn: () => changeRequestsApi.detail(crId),
  });

  const [overridesYaml, setOverridesYaml] = useState("");
  const [overridesParsed, setOverridesParsed] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (data?.operator_overrides) {
      setOverridesYaml(stringifyYaml(data.operator_overrides));
      setOverridesParsed(data.operator_overrides);
    } else {
      setOverridesYaml("");
      setOverridesParsed({});
    }
  }, [data?.id, data?.operator_overrides]);

  const save = useMutation({
    mutationFn: () =>
      changeRequestsApi.update(crId, { operator_overrides: overridesParsed }),
    onSuccess: () => {
      toast.success("운영자 수정안이 저장되었습니다.");
      qc.invalidateQueries({ queryKey: ["change-requests", crId] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "저장 실패"),
  });

  const issue = useMutation({
    mutationFn: (via: "pr" | "issue" | "markdown") => changeRequestsApi.issue(crId, via),
    onSuccess: (res, via) => {
      if (via === "pr" && res.pr_url) {
        toast.success("PR이 발행되었습니다.");
        window.open(res.pr_url, "_blank");
      } else if (via === "issue" && res.issue_url) {
        toast.success("Issue가 발행되었습니다.");
        window.open(res.issue_url, "_blank");
      } else {
        toast.success("처리되었습니다.");
      }
      qc.invalidateQueries({ queryKey: ["change-requests", crId] });
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "발행 실패"),
  });

  async function downloadMarkdown() {
    try {
      const md = await changeRequestsApi.markdown(crId);
      const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `change-request-${crId.slice(0, 8)}.md`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("Markdown을 다운로드했습니다.");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "다운로드 실패");
    }
  }

  if (isLoading) return <Skeleton className="h-[60vh] w-full" />;
  if (error || !data) {
    return (
      <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-6 text-sm">
        <p className="font-medium">변경 요청을 불러올 수 없습니다.</p>
        <p className="mt-1 text-muted-foreground">
          백엔드 `/change-requests/{crId}` 엔드포인트가 준비되지 않았을 수 있습니다.
        </p>
      </div>
    );
  }

  function handleOverridesChange(yamlText: string) {
    setOverridesYaml(yamlText);
    // We don't try to parse YAML in the browser — keep parsed view in sync
    // only for the JSON-free top-level case ({}). Backend re-validates.
    try {
      const trimmed = yamlText.trim();
      if (!trimmed) {
        setOverridesParsed({});
        return;
      }
      // Operator can paste JSON too — try that first.
      if (trimmed.startsWith("{")) {
        setOverridesParsed(JSON.parse(trimmed));
      }
    } catch {
      // Keep last good value; backend will validate.
    }
  }

  return (
    <div className="space-y-4">
      <Header cr={data} />

      <AssistantHandoffPanel changeRequest={data} />

      <div className="grid gap-4 xl:grid-cols-3">
        <StaticColumn cr={data} />
        <AIColumn cr={data} />
        <OperatorColumn
          yaml={overridesYaml}
          onChange={handleOverridesChange}
          onSave={() => save.mutate()}
          saving={save.isPending}
        />
      </div>

      <div className="sticky bottom-4 z-10 flex items-center justify-between gap-3 rounded-lg border bg-card/95 p-4 shadow backdrop-blur">
        <div className="text-xs text-muted-foreground">
          발행 후에는 운영자 수정안이 잠깁니다. 저장하지 않은 변경이 있으면 먼저 저장하세요.
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={downloadMarkdown} disabled={issue.isPending}>
            <Download className="mr-1 h-4 w-4" /> Markdown 다운로드
          </Button>
          <Button
            variant="outline"
            onClick={() => issue.mutate("issue")}
            disabled={issue.isPending}
          >
            <MessageSquare className="mr-1 h-4 w-4" /> Issue 발행
          </Button>
          <Button
            onClick={() => issue.mutate("pr")}
            disabled={issue.isPending}
          >
            {issue.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <GitPullRequest className="mr-1 h-4 w-4" />
            )}
            PR 자동 발행
          </Button>
        </div>
      </div>
    </div>
  );
}

function Header({ cr }: { cr: ChangeRequest }) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-4 p-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">변경 요청 #{cr.id.slice(0, 8)}</h2>
            <StatusBadge status={cr.status} />
          </div>
          <div className="font-mono text-xs text-muted-foreground">{cr.repo_url}</div>
          <div className="text-xs text-muted-foreground">
            commit {cr.commit_sha ? cr.commit_sha.slice(0, 10) : "—"} · 생성{" "}
            {formatDateTime(cr.created_at)}
            {cr.issued_at && <> · 발행 {formatDateTime(cr.issued_at)}</>}
          </div>
        </div>
        {cr.pr_url && (
          <Button asChild size="sm" variant="outline">
            <a href={cr.pr_url} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-1 h-3.5 w-3.5" /> PR 보기
            </a>
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: ChangeRequestStatus }) {
  return <Badge variant={statusColor(status)}>{statusLabel(status)}</Badge>;
}

function StaticColumn({ cr }: { cr: ChangeRequest }) {
  return (
    <Column title="Static Facts" subtitle="결정론적 분석 (회색)" tone="muted">
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words text-[11px] leading-relaxed text-muted-foreground">
        {JSON.stringify(cr.static_facts, null, 2)}
      </pre>
    </Column>
  );
}

function AIColumn({ cr }: { cr: ChangeRequest }) {
  const llm = cr.llm_response;
  const isWaiting =
    cr.status === "awaiting_assistant" ||
    !llm ||
    (Object.keys(llm.manifest_draft ?? {}).length === 0 &&
      (llm.open_questions ?? []).length === 0);

  if (isWaiting) {
    return (
      <Column title="AI Inferred" subtitle="Claude 응답 대기 중" tone="primary">
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-4 text-xs">
          <p className="font-semibold text-amber-800 dark:text-amber-200">
            Claude 응답 대기 중
          </p>
          <p className="mt-1 text-amber-700/80 dark:text-amber-200/80">
            상단의 <strong>AI 분석 핸드오프</strong> 패널에서 패킷을
            다운로드하고, Claude 응답을 붙여넣어 검증하면 이 영역이 자동으로
            채워집니다.
          </p>
        </div>
      </Column>
    );
  }

  return (
    <Column title="AI Inferred" subtitle="신뢰도별 색상 코딩" tone="primary">
      <div className="space-y-4">
        <ManifestDraftPreview
          manifest={llm.manifest_draft}
          confidence={llm.confidence}
        />

        <Section title="확인이 필요한 항목" count={llm.open_questions?.length ?? 0}>
          {(llm.open_questions ?? []).map((q, i) => (
            <OpenQuestionItem key={i} q={q} />
          ))}
        </Section>

        <Section
          title="개발자 요청 파일"
          count={llm.developer_change_request?.required_files?.length ?? 0}
        >
          {(llm.developer_change_request?.required_files ?? []).map((f, i) => (
            <FileBlock key={i} file={f} />
          ))}
        </Section>
      </div>
    </Column>
  );
}

function OperatorColumn({
  yaml,
  onChange,
  onSave,
  saving,
}: {
  yaml: string;
  onChange: (s: string) => void;
  onSave: () => void;
  saving: boolean;
}) {
  return (
    <Column title="Operator Overrides" subtitle="이 값이 최종 manifest를 덮어씁니다" tone="accent">
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          JSON 또는 YAML 형식으로 수정 사항만 입력하세요. 비어 있으면 AI 초안이 그대로 사용됩니다.
        </p>
        <YamlEditor value={yaml} onChange={onChange} rows={20} />
        <Button onClick={onSave} disabled={saving} className="w-full">
          {saving ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-1 h-4 w-4" />
          )}
          운영자 수정안 저장
        </Button>
      </div>
    </Column>
  );
}

function Column({
  title,
  subtitle,
  tone,
  children,
}: {
  title: string;
  subtitle: string;
  tone: "muted" | "primary" | "accent";
  children: React.ReactNode;
}) {
  const toneClass =
    tone === "muted"
      ? "border-muted-foreground/20"
      : tone === "primary"
        ? "border-primary/30"
        : "border-emerald-500/30";
  return (
    <Card className={cn("border-l-4", toneClass)}>
      <CardContent className="space-y-3 p-5">
        <div>
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
            {subtitle}
          </div>
        </div>
        {children}
      </CardContent>
    </Card>
  );
}

function Section({
  title,
  count,
  children,
}: {
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h4>
        <Badge variant="muted">{count}</Badge>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function ManifestDraftPreview({
  manifest,
  confidence,
}: {
  manifest: Record<string, unknown>;
  confidence: Record<string, number>;
}) {
  const entries = Object.entries(manifest);
  return (
    <div className="space-y-1.5">
      {entries.map(([key, value]) => {
        const c = confidence[key] ?? confidence[`${key}.*`];
        const cls = confidenceClasses(c);
        return (
          <div
            key={key}
            className={cn("rounded-md border p-2", cls.bg, cls.border)}
          >
            <div className="flex items-center justify-between gap-2">
              <code className={cn("text-xs font-semibold", cls.text)}>{key}</code>
              <ConfidenceBadge value={c} />
            </div>
            <pre className="mt-1 overflow-auto text-[11px] leading-relaxed">
              {formatValue(value)}
            </pre>
          </div>
        );
      })}
      {entries.length === 0 && (
        <div className="rounded-md bg-muted/30 p-3 text-xs text-muted-foreground">
          manifest 초안이 비어 있습니다.
        </div>
      )}
    </div>
  );
}

function OpenQuestionItem({ q }: { q: OpenQuestion }) {
  return (
    <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
      <div className="flex items-center gap-2">
        <code className="text-xs font-semibold">{q.field}</code>
      </div>
      <p className="mt-1 text-xs">{q.question}</p>
      {q.candidates && q.candidates.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {q.candidates.map((c) => (
            <Badge key={String(c)} variant="muted">
              {String(c)}
            </Badge>
          ))}
        </div>
      )}
      {q.context && (
        <p className="mt-1 text-[11px] text-muted-foreground">{q.context}</p>
      )}
    </div>
  );
}

function FileBlock({ file }: { file: RequiredFile }) {
  return (
    <details className="rounded-md border bg-muted/30">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs">
        <FileText className="h-3.5 w-3.5" />
        <code className="font-semibold">{file.path}</code>
        <Badge variant="muted">{file.kind}</Badge>
        {file.mode && <Badge variant="outline">{file.mode}</Badge>}
      </summary>
      <pre className="max-h-72 overflow-auto border-t bg-background p-3 text-[11px] leading-relaxed">
        {file.content}
      </pre>
    </details>
  );
}

function formatValue(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

function stringifyYaml(obj: Record<string, unknown>): string {
  if (!obj || Object.keys(obj).length === 0) return "";
  // Prefer JSON — operator can switch to YAML on save (backend handles parsing).
  return JSON.stringify(obj, null, 2);
}
