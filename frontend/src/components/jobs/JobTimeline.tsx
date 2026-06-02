import type { JobDetail } from "@/lib/api/types";
import { formatDateTime, formatDuration } from "@/lib/utils/format";

export function JobTimeline({ job }: { job: JobDetail }) {
  const entries = [
    { label: "생성", at: job.created_at },
    { label: "시작", at: job.started_at },
    { label: "종료", at: job.finished_at },
  ];
  return (
    <div className="space-y-3 rounded-lg border bg-card p-4">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        타임라인
      </div>
      <ul className="space-y-2.5">
        {entries.map((e) => (
          <li key={e.label} className="flex items-center justify-between gap-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full bg-primary" />
              <span className="text-muted-foreground">{e.label}</span>
            </div>
            <span className="font-mono text-xs">{formatDateTime(e.at)}</span>
          </li>
        ))}
      </ul>
      <div className="border-t pt-2.5 text-sm">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">소요 시간</span>
          <span className="font-mono text-xs">{formatDuration(job.duration_sec)}</span>
        </div>
      </div>
    </div>
  );
}
