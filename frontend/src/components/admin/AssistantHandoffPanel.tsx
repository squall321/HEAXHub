import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileText,
  Loader2,
  Package,
  RefreshCw,
  Send,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { changeRequestsApi } from "@/lib/api/changeRequests";
import { ApiError, type ChangeRequest } from "@/lib/api/types";
import {
  averageConfidence,
  formatConfidence,
} from "@/lib/utils/confidence";
import { formatDateTime } from "@/lib/utils/format";
import { cn } from "@/lib/utils/cn";

interface AssistantHandoffPanelProps {
  changeRequest: ChangeRequest;
}

/**
 * Claude-in-the-loop handoff:
 *   1. operator downloads a .zip analysis packet
 *   2. pastes it into their own Claude/AI chat
 *   3. pastes the resulting JSON/yaml+markdown back into the textbox here
 *
 * No LLM API calls are made from this component.
 */
export function AssistantHandoffPanel({ changeRequest }: AssistantHandoffPanelProps) {
  const cr = changeRequest;
  const qc = useQueryClient();
  const [rawText, setRawText] = useState("");
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitDetails, setSubmitDetails] = useState<unknown>(null);
  const [instructionsOpen, setInstructionsOpen] = useState(false);
  const [downloadedOnce, setDownloadedOnce] = useState(false);

  const packetAvailable = cr.assistant_packet_available ?? true;

  const downloadMut = useMutation({
    mutationFn: () => changeRequestsApi.downloadPacket(cr.id),
    onSuccess: (blob) => {
      if (!(blob instanceof Blob) || blob.size === 0) {
        toast.error("패킷이 비어 있습니다. 백엔드를 확인하세요.");
        return;
      }
      const filename = `heaxhub-packet-${cr.id?.slice(0, 8) ?? "unknown"}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setDownloadedOnce(true);
      toast.success("패킷을 다운로드했습니다.");
    },
    onError: (e) =>
      toast.error(e instanceof Error ? e.message : "패킷 다운로드 실패"),
  });

  const instructionsQuery = useQuery({
    queryKey: ["change-requests", cr.id, "instructions"],
    queryFn: () => changeRequestsApi.getInstructions(cr.id),
    enabled: instructionsOpen,
    staleTime: 60_000,
  });

  const submitMut = useMutation({
    mutationFn: () => changeRequestsApi.submitAssistantResponse(cr.id, rawText),
    onSuccess: () => {
      toast.success("응답이 반영되었습니다.");
      setSubmitError(null);
      setSubmitDetails(null);
      setRawText("");
      qc.invalidateQueries({ queryKey: ["change-requests", cr.id] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
    },
    onError: (e) => {
      const message = e instanceof Error ? e.message : "응답 저장 실패";
      setSubmitError(message);
      setSubmitDetails(e instanceof ApiError ? e.details : null);
    },
  });

  function handleSubmit() {
    if (!rawText.trim()) {
      setSubmitError("응답을 먼저 붙여 넣으세요.");
      setSubmitDetails(null);
      return;
    }
    setSubmitError(null);
    setSubmitDetails(null);
    submitMut.mutate();
  }

  const avgConfidence = averageConfidence(cr.llm_response?.confidence);
  const manifest = cr.final_manifest ?? {};
  const respondedAt = cr.assistant_responded_at ?? null;

  return (
    <Card className="border-l-4 border-sky-500/40">
      <CardContent className="space-y-5 p-5">
        <div className="flex items-center gap-2">
          <Package className="h-4 w-4 text-sky-600 dark:text-sky-300" />
          <h3 className="text-sm font-semibold uppercase tracking-wider">
            AI 분석 핸드오프
          </h3>
          <span className="text-[11px] text-muted-foreground">
            (LLM API 호출 없이, 운영자가 직접 Claude와 대화하는 방식)
          </span>
        </div>

        {/* Panel 1: 패킷 다운로드 ------------------------------------------------- */}
        <section className="space-y-2 rounded-md border bg-muted/20 p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-700 dark:text-sky-300">
              1
            </span>
            패킷 다운로드
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button
              size="lg"
              onClick={() => downloadMut.mutate()}
              disabled={downloadMut.isPending || !packetAvailable}
            >
              {downloadMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Download className="mr-2 h-4 w-4" />
              )}
              AI 분석 패킷 다운로드 (.zip)
            </Button>
            <button
              type="button"
              onClick={() => setInstructionsOpen(true)}
              className="text-xs text-primary hover:underline"
            >
              지시사항 미리보기
            </button>
            {!packetAvailable && (
              <Badge variant="warning">패킷이 아직 생성되지 않았습니다</Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            Claude 또는 다른 AI 챗에 zip을 첨부하고{" "}
            <span className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              instructions.md 시키는 대로 분석해 줘
            </span>{" "}
            라고 보내세요.
          </p>
          {downloadedOnce && (
            <div className="rounded-md border border-sky-500/40 bg-sky-500/5 px-3 py-2 text-xs text-sky-800 dark:text-sky-200">
              분석 후 응답을 아래 텍스트박스에 그대로 붙여 넣으세요.
            </div>
          )}
        </section>

        {/* Panel 2: 응답 붙여넣기 ----------------------------------------------- */}
        <section className="space-y-2 rounded-md border bg-muted/20 p-4">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-700 dark:text-sky-300">
              2
            </span>
            응답 붙여넣기
          </div>
          <Textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value)}
            placeholder="Claude가 준 응답 (JSON 또는 yaml+markdown) 을 그대로 붙여 넣으세요"
            className="min-h-[240px] font-mono text-xs leading-relaxed"
            spellCheck={false}
          />
          <div className="flex items-center justify-between gap-3">
            <div className="text-[11px] text-muted-foreground">
              {rawText.length > 0
                ? `${rawText.length.toLocaleString()} 문자`
                : "비어 있음"}
            </div>
            <Button
              onClick={handleSubmit}
              disabled={submitMut.isPending || !rawText.trim()}
            >
              {submitMut.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Send className="mr-2 h-4 w-4" />
              )}
              검증 + 저장
            </Button>
          </div>
          {submitError && (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/5 p-3 text-xs text-rose-800 dark:text-rose-200">
              <div className="font-semibold">검증 실패</div>
              <div className="mt-1 whitespace-pre-wrap break-words">
                {submitError}
              </div>
              {submitDetails != null && (
                <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-rose-950/10 p-2 font-mono text-[10px] leading-relaxed">
                  {formatDetails(submitDetails)}
                </pre>
              )}
            </div>
          )}
        </section>

        {/* Panel 3: 현재 상태 -------------------------------------------------- */}
        <section className="space-y-2 rounded-md border bg-muted/20 p-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <span className="rounded bg-sky-500/15 px-1.5 py-0.5 text-sky-700 dark:text-sky-300">
                3
              </span>
              현재 상태
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => downloadMut.mutate()}
              disabled={downloadMut.isPending || !packetAvailable}
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
              다시 분석 요청
            </Button>
          </div>
          <div className="grid gap-2 text-xs sm:grid-cols-3">
            <StatusCell
              label="마지막 응답"
              value={respondedAt ? formatDateTime(respondedAt) : "—"}
            />
            <StatusCell
              label="평균 confidence"
              value={
                avgConfidence != null ? formatConfidence(avgConfidence) : "—"
              }
              tone={confidenceTone(avgConfidence)}
            />
            <StatusCell
              label="manifest"
              value={summarizeManifest(manifest)}
              mono
            />
          </div>
        </section>
      </CardContent>

      <InstructionsDialog
        open={instructionsOpen}
        onOpenChange={setInstructionsOpen}
        loading={instructionsQuery.isLoading}
        text={
          typeof instructionsQuery.data === "string"
            ? instructionsQuery.data
            : ""
        }
        error={
          instructionsQuery.error instanceof Error
            ? instructionsQuery.error.message
            : null
        }
      />
    </Card>
  );
}

function StatusCell({
  label,
  value,
  tone,
  mono,
}: {
  label: string;
  value: string;
  tone?: "green" | "yellow" | "red";
  mono?: boolean;
}) {
  const toneClass =
    tone === "green"
      ? "text-emerald-700 dark:text-emerald-300"
      : tone === "yellow"
        ? "text-amber-700 dark:text-amber-300"
        : tone === "red"
          ? "text-rose-700 dark:text-rose-300"
          : "";
  return (
    <div className="rounded-md border bg-background p-2">
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "mt-0.5 break-words text-xs font-medium",
          mono && "font-mono",
          toneClass,
        )}
      >
        {value}
      </div>
    </div>
  );
}

function InstructionsDialog({
  open,
  onOpenChange,
  loading,
  text,
  error,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  loading: boolean;
  text: string;
  error: string | null;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="h-4 w-4" />
            instructions.md 미리보기
          </DialogTitle>
        </DialogHeader>
        <div className="mt-2">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              불러오는 중...
            </div>
          ) : error ? (
            <div className="rounded-md border border-rose-500/40 bg-rose-500/5 p-3 text-xs text-rose-800 dark:text-rose-200">
              {error}
            </div>
          ) : text.trim().length === 0 ? (
            <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
              지시사항이 비어 있습니다.
            </div>
          ) : (
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/20 p-3 font-mono text-[11px] leading-relaxed">
              {text}
            </pre>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function summarizeManifest(m: Record<string, unknown>): string {
  if (!m || Object.keys(m).length === 0) return "—";
  const id = (m.id as string) ?? "(no id)";
  const appType = (m.app_type as string) ?? "?";
  const build = m.build as Record<string, unknown> | undefined;
  const buildType = (build?.type as string) ?? "?";
  return `${id} · ${appType} · build:${buildType}`;
}

function confidenceTone(
  c: number | null,
): "green" | "yellow" | "red" | undefined {
  if (c == null) return undefined;
  if (c >= 0.9) return "green";
  if (c >= 0.7) return "yellow";
  return "red";
}

function formatDetails(d: unknown): string {
  if (d == null) return "";
  if (typeof d === "string") return d;
  try {
    return JSON.stringify(d, null, 2);
  } catch {
    return String(d);
  }
}
