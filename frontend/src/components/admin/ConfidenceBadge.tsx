import { Badge } from "@/components/ui/badge";
import { confidenceColor, formatConfidence } from "@/lib/utils/confidence";

interface ConfidenceBadgeProps {
  value?: number | null;
  showLabel?: boolean;
}

export function ConfidenceBadge({ value, showLabel = false }: ConfidenceBadgeProps) {
  const level = confidenceColor(value);
  const variant = level === "green" ? "success" : level === "yellow" ? "warning" : "destructive";
  return (
    <Badge variant={variant}>
      {formatConfidence(value)}
      {showLabel && (
        <span className="ml-1 text-[10px] opacity-80">
          {level === "green" ? "자동" : level === "yellow" ? "검토" : "필수"}
        </span>
      )}
    </Badge>
  );
}
