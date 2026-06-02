import { Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AppListQuery } from "@/lib/api/apps";
import type { AppType, ExecutionTarget, Visibility } from "@/lib/api/types";
import { categoryLabel } from "@/styles/tokens";

interface AppFiltersProps {
  value: AppListQuery;
  onChange: (next: AppListQuery) => void;
}

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

const VISIBILITIES: Visibility[] = ["private", "team", "department", "company"];

const STATUSES = ["stable", "beta", "deprecated"];

export function AppFilters({ value, onChange }: AppFiltersProps) {
  const update = <K extends keyof AppListQuery>(key: K, v: AppListQuery[K]) =>
    onChange({ ...value, [key]: v, page: 1 });

  const reset = () => onChange({});

  return (
    <aside className="space-y-5 lg:sticky lg:top-20">
      <div>
        <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          검색
        </Label>
        <div className="relative mt-2">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            value={value.q ?? ""}
            onChange={(e) => update("q", e.target.value)}
            placeholder="이름·태그·설명"
            className="pl-8"
          />
        </div>
      </div>

      <FilterSelect
        label="앱 유형"
        value={value.app_type ?? ""}
        onChange={(v) => update("app_type", (v || undefined) as AppType | undefined)}
        options={[{ value: "", label: "전체" }].concat(
          APP_TYPES.map((t) => ({ value: t, label: categoryLabel[t] })),
        )}
      />
      <FilterSelect
        label="실행 환경"
        value={value.execution_target ?? ""}
        onChange={(v) =>
          update("execution_target", (v || undefined) as ExecutionTarget | undefined)
        }
        options={[{ value: "", label: "전체" }].concat(
          TARGETS.map((t) => ({ value: t, label: t })),
        )}
      />
      <FilterSelect
        label="공개 범위"
        value={value.visibility ?? ""}
        onChange={(v) => update("visibility", (v || undefined) as Visibility | undefined)}
        options={[{ value: "", label: "전체" }].concat(
          VISIBILITIES.map((t) => ({ value: t, label: t })),
        )}
      />
      <FilterSelect
        label="상태"
        value={value.status ?? ""}
        onChange={(v) => update("status", v || undefined)}
        options={[{ value: "", label: "전체" }].concat(
          STATUSES.map((t) => ({ value: t, label: t })),
        )}
      />

      <Button variant="ghost" size="sm" onClick={reset} className="w-full">
        <X className="mr-2 h-3.5 w-3.5" /> 필터 초기화
      </Button>
    </aside>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div>
      <Label className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </Label>
      <Select value={value || "__all__"} onValueChange={(v) => onChange(v === "__all__" ? "" : v)}>
        <SelectTrigger className="mt-2">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value || "__all__"} value={o.value || "__all__"}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
