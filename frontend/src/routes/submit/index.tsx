import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { motion } from "framer-motion";
import { Check, ChevronLeft, ChevronRight, Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { ConfidenceBadge } from "@/components/admin/ConfidenceBadge";
import { RequireAuth } from "@/components/common/RequireAuth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { changeRequestsApi } from "@/lib/api/changeRequests";
import { submissionsApi } from "@/lib/api/submissions";
import type {
  AppType,
  ChangeRequest,
  ExecutionTarget,
  SourceType,
} from "@/lib/api/types";
import { categoryLabel } from "@/styles/tokens";
import { cn } from "@/lib/utils/cn";

const APP_TYPES: AppType[] = [
  "cli_tool",
  "web_app",
  "windows_gui",
  "remote_app",
  "external_link",
  "slurm_job",
  "container_app",
];
const TARGETS: ExecutionTarget[] = [
  "linux_runner",
  "slurm",
  "apptainer",
  "windows_worker",
  "external_url",
  "local_pc",
];

const SOURCE_TYPES: { value: SourceType; label: string; help: string }[] = [
  { value: "git", label: "Git 저장소", help: "사내 GitLab / GitHub 등 clone 가능한 URL" },
  { value: "archive_url", label: "Archive URL", help: "ZIP/TAR 정적 링크 (NAS, S3 등)" },
  { value: "local_path", label: "로컬 경로", help: "서버 디스크에 이미 존재하는 디렉터리" },
  { value: "system_command", label: "시스템 명령", help: "이미 설치된 도구 (예: matlab, vim)" },
];

const schema = z
  .object({
    name: z.string().min(2, "이름을 입력하세요"),
    proposed_app_id: z
      .string()
      .regex(/^[a-z][a-z0-9_]{2,63}$/, "snake_case (영소문자/숫자/_) 3~64자"),
    description: z.string().optional(),
    source_type: z.enum(["git", "archive_url", "local_path", "system_command"]),
    upstream_repo_url: z.string().optional(),
    archive_url: z.string().optional(),
    archive_sha256: z.string().optional(),
    local_path: z.string().optional(),
    verify_command: z.string().optional(),
    app_type: z.enum(APP_TYPES as [AppType, ...AppType[]]),
    execution_target: z.enum(TARGETS as [ExecutionTarget, ...ExecutionTarget[]]),
  })
  .refine(
    (v) => {
      if (v.source_type === "git") return Boolean(v.upstream_repo_url);
      if (v.source_type === "archive_url") return Boolean(v.archive_url);
      if (v.source_type === "local_path") return Boolean(v.local_path);
      if (v.source_type === "system_command") return Boolean(v.verify_command);
      return true;
    },
    { message: "소스 정보를 입력하세요", path: ["upstream_repo_url"] },
  );

type Values = z.infer<typeof schema>;

const STEPS = ["기본 정보", "소스 유형", "소스 정보", "분류", "감지 미리보기", "검토"] as const;

export const Route = createFileRoute("/submit/")({
  component: () => (
    <RequireAuth>
      <SubmitPage />
    </RequireAuth>
  ),
});

function SubmitPage() {
  const [step, setStep] = useState(0);
  const [previewCr, setPreviewCr] = useState<ChangeRequest | null>(null);
  const navigate = useNavigate();

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      proposed_app_id: "",
      description: "",
      source_type: "git",
      upstream_repo_url: "",
      archive_url: "",
      archive_sha256: "",
      local_path: "",
      verify_command: "",
      app_type: "cli_tool",
      execution_target: "linux_runner",
    },
    mode: "onChange",
  });

  const preview = useMutation({
    mutationFn: () => {
      const v = form.getValues();
      const repoUrl = pickRepoUrl(v);
      return changeRequestsApi.create({
        repo_url: repoUrl,
        source_type: v.source_type,
        source_config: buildSourceConfig(v),
      });
    },
    onSuccess: (cr) => {
      setPreviewCr(cr);
      toast.success("AI 분석이 완료되었습니다.");
    },
    onError: (e) =>
      toast.error(
        e instanceof Error
          ? `미리보기 실패: ${e.message} (백엔드 준비 전이면 다음 단계로 건너뛸 수 있습니다)`
          : "미리보기 실패",
      ),
  });

  const submit = useMutation({
    mutationFn: (v: Values) =>
      submissionsApi.create({
        proposed_app_id: v.proposed_app_id,
        name: v.name,
        description: v.description,
        upstream_repo_url: pickRepoUrl(v),
        app_type: v.app_type,
        execution_target: v.execution_target,
        source_config: buildSourceConfig(v),
      }),
    onSuccess: () => {
      toast.success("신청이 접수되었습니다. 운영자 검토를 기다려 주세요.");
      navigate({ to: "/" });
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "신청 실패"),
  });

  const next = async () => {
    const fields: Array<keyof Values>[] = [
      ["name", "proposed_app_id", "description"],
      ["source_type"],
      ["upstream_repo_url", "archive_url", "local_path", "verify_command"],
      ["app_type", "execution_target"],
      [],
      [],
    ];
    const ok = await form.trigger(fields[step]);
    if (!ok) return;
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const prev = () => setStep((s) => Math.max(s - 1, 0));

  const onSubmit = form.handleSubmit((v) => submit.mutate(v));

  const v = form.watch();

  return (
    <div className="mx-auto max-w-3xl px-6 py-8 md:px-10">
      <h1 className="text-3xl font-bold tracking-tight">새 앱 신청</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        소스가 git 저장소가 아니어도 등록 가능합니다. AI가 분석해 manifest 초안을 제안합니다.
      </p>

      {/* Stepper */}
      <div className="mt-8 flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <div
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-bold",
                i < step && "border-emerald-500 bg-emerald-500 text-white",
                i === step && "border-primary bg-primary text-primary-foreground",
                i > step && "border-border text-muted-foreground",
              )}
            >
              {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
            </div>
            <span
              className={cn(
                "hidden text-xs font-semibold uppercase tracking-wider sm:inline",
                i === step ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
            {i < STEPS.length - 1 && <div className="h-px flex-1 bg-border" />}
          </div>
        ))}
      </div>

      <Form {...form}>
        <form onSubmit={onSubmit}>
          <motion.div
            key={step}
            initial={{ opacity: 0, x: 6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2 }}
            className="mt-6"
          >
            <Card>
              <CardContent className="space-y-5 pt-6">
                {step === 0 && (
                  <>
                    <FormField
                      control={form.control}
                      name="name"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>앱 이름</FormLabel>
                          <FormControl>
                            <Input placeholder="LS-DYNA K File Checker" {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="proposed_app_id"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>제안 App ID</FormLabel>
                          <FormControl>
                            <Input placeholder="lsdyna_kfile_checker" {...field} />
                          </FormControl>
                          <FormDescription>
                            소문자 snake_case. 운영자가 변경할 수 있습니다.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="description"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>한 줄 설명</FormLabel>
                          <FormControl>
                            <Textarea rows={3} {...field} />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </>
                )}

                {step === 1 && (
                  <FormField
                    control={form.control}
                    name="source_type"
                    render={({ field }) => (
                      <FormItem className="space-y-3">
                        <FormLabel>소스 유형</FormLabel>
                        <FormControl>
                          <RadioGroup
                            value={field.value}
                            onValueChange={field.onChange}
                            className="grid gap-2"
                          >
                            {SOURCE_TYPES.map((s) => (
                              <Label
                                key={s.value}
                                className={cn(
                                  "flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors hover:bg-accent",
                                  field.value === s.value && "border-primary bg-primary/5",
                                )}
                              >
                                <RadioGroupItem value={s.value} className="mt-1" />
                                <div className="space-y-0.5">
                                  <div className="text-sm font-semibold">{s.label}</div>
                                  <div className="text-xs text-muted-foreground">{s.help}</div>
                                </div>
                              </Label>
                            ))}
                          </RadioGroup>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}

                {step === 2 && (
                  <SourceInfoStep
                    sourceType={v.source_type}
                    control={form.control}
                  />
                )}

                {step === 3 && (
                  <>
                    <FormField
                      control={form.control}
                      name="app_type"
                      render={() => (
                        <FormItem>
                          <FormLabel>앱 유형</FormLabel>
                          <Controller
                            control={form.control}
                            name="app_type"
                            render={({ field: f }) => (
                              <Select value={f.value} onValueChange={f.onChange}>
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {APP_TYPES.map((t) => (
                                    <SelectItem key={t} value={t}>
                                      {categoryLabel[t]}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          />
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="execution_target"
                      render={() => (
                        <FormItem>
                          <FormLabel>실행 환경</FormLabel>
                          <Controller
                            control={form.control}
                            name="execution_target"
                            render={({ field: f }) => (
                              <Select value={f.value} onValueChange={f.onChange}>
                                <SelectTrigger>
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {TARGETS.map((t) => (
                                    <SelectItem key={t} value={t}>
                                      {t}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            )}
                          />
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </>
                )}

                {step === 4 && (
                  <PreviewStep
                    cr={previewCr}
                    isPending={preview.isPending}
                    onRun={() => preview.mutate()}
                  />
                )}

                {step === 5 && (
                  <div className="space-y-3 text-sm">
                    <Row label="이름" value={v.name} />
                    <Row label="App ID" value={<code>{v.proposed_app_id}</code>} />
                    <Row label="설명" value={v.description || "—"} />
                    <Row label="소스 유형" value={v.source_type} />
                    <Row label="소스" value={<code>{pickRepoUrl(v)}</code>} />
                    {v.archive_sha256 && (
                      <Row label="SHA256" value={<code className="text-xs">{v.archive_sha256}</code>} />
                    )}
                    <Row label="앱 유형" value={categoryLabel[v.app_type]} />
                    <Row label="실행 환경" value={v.execution_target} />
                    {previewCr && (
                      <Row
                        label="AI 분석"
                        value={
                          <Badge variant="info">
                            <Sparkles className="mr-1 h-3 w-3" />
                            CR {previewCr.id.slice(0, 8)}
                          </Badge>
                        }
                      />
                    )}
                  </div>
                )}
              </CardContent>
            </Card>
          </motion.div>

          <div className="mt-6 flex items-center justify-between">
            <Button type="button" variant="ghost" disabled={step === 0} onClick={prev}>
              <ChevronLeft className="mr-1 h-4 w-4" /> 이전
            </Button>
            {step < STEPS.length - 1 ? (
              <Button type="button" onClick={next}>
                다음 <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            ) : (
              <Button type="submit" disabled={submit.isPending}>
                {submit.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                신청 제출
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  );
}

function SourceInfoStep({
  sourceType,
  control,
}: {
  sourceType: SourceType;
  // biome-ignore lint/suspicious/noExplicitAny: react-hook-form control type is parameterized
  control: any;
}) {
  if (sourceType === "git") {
    return (
      <FormField
        control={control}
        name="upstream_repo_url"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Upstream Git URL</FormLabel>
            <FormControl>
              <Input
                placeholder="https://git.company.com/team/your-tool.git"
                {...field}
              />
            </FormControl>
            <FormDescription>
              사내 도메인만 허용됩니다. clone 가능 여부가 자동 검증됩니다.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    );
  }
  if (sourceType === "archive_url") {
    return (
      <>
        <FormField
          control={control}
          name="archive_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Archive URL</FormLabel>
              <FormControl>
                <Input
                  placeholder="https://nas.company.com/share/your-tool-v1.zip"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={control}
          name="archive_sha256"
          render={({ field }) => (
            <FormItem>
              <FormLabel>SHA256 (선택)</FormLabel>
              <FormControl>
                <Input placeholder="비워두면 다운로드 시 자동 계산" {...field} />
              </FormControl>
              <FormDescription>무결성 검증용 해시. 비워두면 등록 시 계산됩니다.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </>
    );
  }
  if (sourceType === "local_path") {
    return (
      <FormField
        control={control}
        name="local_path"
        render={({ field }) => (
          <FormItem>
            <FormLabel>로컬 경로</FormLabel>
            <FormControl>
              <Input placeholder="/srv/shared/your-tool" {...field} />
            </FormControl>
            <FormDescription>
              서버 디스크의 절대 경로. 권한이 있는 디렉터리만 사용 가능합니다.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />
    );
  }
  return (
    <FormField
      control={control}
      name="verify_command"
      render={({ field }) => (
        <FormItem>
          <FormLabel>검증 명령</FormLabel>
          <FormControl>
            <Input placeholder="matlab -nodisplay -r 'exit'" {...field} />
          </FormControl>
          <FormDescription>
            도구가 호스트에 설치되어 있는지 확인하기 위해 실행할 명령.
          </FormDescription>
          <FormMessage />
        </FormItem>
      )}
    />
  );
}

function PreviewStep({
  cr,
  isPending,
  onRun,
}: {
  cr: ChangeRequest | null;
  isPending: boolean;
  onRun: () => void;
}) {
  if (isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-6 w-32" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (!cr) {
    return (
      <div className="space-y-3">
        <div className="rounded-md bg-muted/40 p-4 text-sm">
          <p className="font-medium">AI 분석을 실행해 manifest 초안을 미리 확인하세요.</p>
          <p className="mt-1 text-xs text-muted-foreground">
            저장소를 받아 static 분석 + LLM 추론을 1~2분 내에 수행합니다. 결과는 신청서에 첨부됩니다.
            (백엔드가 준비되지 않았다면 건너뛰고 다음 단계로 진행할 수 있습니다.)
          </p>
        </div>
        <Button type="button" onClick={onRun}>
          <Sparkles className="mr-1 h-4 w-4" /> AI 분석 실행
        </Button>
      </div>
    );
  }

  const llm = cr.llm_response;
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3 text-sm">
        AI 분석 완료. CR ID <code className="text-xs">{cr.id}</code>
      </div>

      <div>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Manifest 초안
        </h4>
        <div className="space-y-1.5">
          {Object.entries(llm.manifest_draft).map(([key, value]) => (
            <div key={key} className="flex items-start justify-between gap-2 rounded-md border p-2">
              <div className="min-w-0 flex-1">
                <code className="text-xs font-semibold">{key}</code>
                <pre className="mt-0.5 overflow-auto text-[11px] text-muted-foreground">
                  {typeof value === "string" ? value : JSON.stringify(value)}
                </pre>
              </div>
              <ConfidenceBadge value={llm.confidence[key]} />
            </div>
          ))}
        </div>
      </div>

      {(llm.open_questions ?? []).length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            확인이 필요한 항목 ({llm.open_questions.length})
          </h4>
          <div className="space-y-1.5">
            {llm.open_questions.map((q, i) => (
              <div key={i} className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs">
                <code className="font-semibold">{q.field}</code>: {q.question}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function pickRepoUrl(v: Values): string {
  if (v.source_type === "git") return v.upstream_repo_url ?? "";
  if (v.source_type === "archive_url") return v.archive_url ?? "";
  if (v.source_type === "local_path") return v.local_path ?? "";
  return v.verify_command ?? "";
}

function buildSourceConfig(v: Values) {
  if (v.source_type === "git") {
    return { type: v.source_type, url: v.upstream_repo_url };
  }
  if (v.source_type === "archive_url") {
    return {
      type: v.source_type,
      url: v.archive_url,
      sha256: v.archive_sha256 || undefined,
    };
  }
  if (v.source_type === "local_path") {
    return { type: v.source_type, path: v.local_path };
  }
  return { type: v.source_type, verify_command: v.verify_command };
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-3 border-b py-2 last:border-0">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div>{value}</div>
    </div>
  );
}
