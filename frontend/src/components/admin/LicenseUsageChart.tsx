import { useQuery } from "@tanstack/react-query";
import { licensesApi } from "@/lib/api/licenses";

interface LicenseUsageChartProps {
  name: string;
  hours?: number;
  width?: number;
  height?: number;
}

/**
 * Tiny SVG sparkline of license token usage.
 * No external chart library — keeps bundle lean.
 */
export function LicenseUsageChart({
  name,
  hours = 24,
  width = 200,
  height = 40,
}: LicenseUsageChartProps) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["admin", "licenses", name, "usage", hours],
    queryFn: () => licensesApi.usage(name, { hours }),
    refetchInterval: 60_000,
  });

  if (isLoading) {
    return <div className="h-10 w-[200px] animate-pulse rounded bg-muted/60" />;
  }
  if (error || !data || data.length === 0) {
    return (
      <div className="flex h-10 w-[200px] items-center justify-center rounded bg-muted/40 text-[10px] text-muted-foreground">
        데이터 없음
      </div>
    );
  }

  const total = data[0]?.total ?? 1;
  const maxY = Math.max(total, ...data.map((d) => d.in_use));
  const points = data.map((d, i) => {
    const x = (i / Math.max(1, data.length - 1)) * width;
    const y = height - (d.in_use / maxY) * (height - 4) - 2;
    return [x, y] as const;
  });

  const path = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(" ");

  const peak = Math.max(...data.map((d) => d.in_use));
  const avg = data.reduce((s, d) => s + d.in_use, 0) / data.length;
  const totalLine = height - (total / maxY) * (height - 4) - 2;

  return (
    <div className="flex items-center gap-2">
      <svg width={width} height={height} className="overflow-visible">
        <line
          x1={0}
          x2={width}
          y1={totalLine}
          y2={totalLine}
          stroke="currentColor"
          strokeOpacity={0.15}
          strokeDasharray="2 2"
        />
        <path d={path} fill="none" stroke="currentColor" strokeWidth={1.5} className="text-primary" />
      </svg>
      <div className="text-[10px] leading-tight text-muted-foreground">
        <div>피크 {peak}</div>
        <div>평균 {avg.toFixed(1)}</div>
      </div>
    </div>
  );
}
