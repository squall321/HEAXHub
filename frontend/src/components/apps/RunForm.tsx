import { useNavigate } from "@tanstack/react-router";
import { ExternalLink, Loader2, Play, Upload } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { appsApi } from "@/lib/api/apps";
import type { Manifest, ManifestInput } from "@/lib/api/types";

interface RunFormProps {
  appId: string;
  manifest: Manifest;
}

export function RunForm({ appId, manifest }: RunFormProps) {
  const navigate = useNavigate();
  const [submitting, setSubmitting] = useState(false);
  const isService = manifest.launch?.mode === "service";
  const defaults = buildDefaults(manifest.inputs ?? []);

  const { register, handleSubmit, control, formState } = useForm({ defaultValues: defaults });

  const onSubmit = handleSubmit(async (values) => {
    setSubmitting(true);
    try {
      const params: Record<string, unknown> = {};
      const files: File[] = [];

      for (const inp of manifest.inputs ?? []) {
        const v = values[inp.name];
        if (inp.type === "file" || inp.type === "folder") {
          if (v instanceof FileList && v.length > 0) {
            for (const f of Array.from(v)) {
              files.push(f);
            }
          }
        } else {
          params[inp.name] = v;
        }
      }

      const res = await appsApi.run(appId, { params, files });
      if (!res?.id) {
        toast.error("실행 응답에 job id가 없습니다.");
        return;
      }
      toast.success(`작업 ${res.id.slice(0, 8)} 이 큐에 적재되었습니다.`);
      navigate({ to: "/jobs/$jobId", params: { jobId: res.id } });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "실행 실패");
    } finally {
      setSubmitting(false);
    }
  });

  if (isService) {
    const openService = () => {
      window.open(`/apps/${appId}/`, "_blank");
    };
    return (
      <Card>
        <CardHeader>
          <CardTitle>서비스 앱</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-4 py-10">
          <p className="text-sm text-muted-foreground">
            이 앱은 장기 실행 서비스(launch.mode=service)로 등록되어 있어 작업 제출 없이 바로 열 수
            있습니다.
          </p>
          <Button onClick={openService}>
            <ExternalLink className="mr-2 h-4 w-4" />
            열기
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (!manifest.inputs || manifest.inputs.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-4 py-12">
          <p className="text-sm text-muted-foreground">이 앱은 추가 입력이 필요하지 않습니다.</p>
          <Button onClick={() => onSubmit()} disabled={submitting}>
            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
            바로 실행
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <form onSubmit={onSubmit}>
      <Card>
        <CardHeader>
          <CardTitle>실행 매개변수</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          {manifest.inputs.map((inp) => (
            <FieldRow key={inp.name} input={inp} register={register} control={control} />
          ))}
        </CardContent>
      </Card>

      <div className="mt-4 flex items-center justify-end gap-3">
        <Button type="submit" disabled={submitting || formState.isSubmitting}>
          {submitting ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Play className="mr-2 h-4 w-4" />
          )}
          실행
        </Button>
      </div>
    </form>
  );
}

function FieldRow({
  input,
  register,
  control,
}: {
  input: ManifestInput;
  register: ReturnType<typeof useForm>["register"];
  control: ReturnType<typeof useForm>["control"];
}) {
  return (
    <div className="grid gap-2">
      <Label className="flex items-center gap-2">
        {input.label ?? input.name}
        {input.required && <span className="text-xs text-destructive">*</span>}
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
          {input.type}
        </span>
      </Label>
      {input.description && (
        <p className="text-xs text-muted-foreground">{input.description}</p>
      )}

      {input.type === "file" || input.type === "folder" ? (
        <div className="flex items-center gap-2">
          <Input
            type="file"
            multiple={input.type === "folder"}
            accept={input.extensions?.join(",")}
            {...register(input.name, { required: input.required })}
          />
          <Upload className="h-4 w-4 text-muted-foreground" />
        </div>
      ) : input.type === "boolean" ? (
        <Controller
          control={control}
          name={input.name}
          render={({ field }) => (
            <div className="flex items-center gap-2">
              <Checkbox
                checked={Boolean(field.value)}
                onCheckedChange={field.onChange}
              />
              <span className="text-sm text-muted-foreground">활성화</span>
            </div>
          )}
        />
      ) : input.type === "enum" ? (
        <Controller
          control={control}
          name={input.name}
          render={({ field }) => (
            <Select value={String(field.value ?? "")} onValueChange={field.onChange}>
              <SelectTrigger>
                <SelectValue placeholder="선택" />
              </SelectTrigger>
              <SelectContent>
                {(input.options ?? []).map((o) => (
                  <SelectItem key={String(o)} value={String(o)}>
                    {String(o)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        />
      ) : input.type === "number" || input.type === "integer" ? (
        <Input
          type="number"
          step={input.type === "integer" ? 1 : "any"}
          min={input.min}
          max={input.max}
          {...register(input.name, {
            required: input.required,
            valueAsNumber: true,
          })}
        />
      ) : input.type === "string" && input.description && input.description.length > 60 ? (
        <Textarea rows={3} {...register(input.name, { required: input.required })} />
      ) : (
        <Input type="text" {...register(input.name, { required: input.required })} />
      )}
    </div>
  );
}

function buildDefaults(inputs: ManifestInput[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const inp of inputs) {
    if (inp.default !== undefined) out[inp.name] = inp.default;
    else if (inp.type === "boolean") out[inp.name] = false;
    else out[inp.name] = "";
  }
  return out;
}
