/**
 * Confidence helpers — used by ChangeRequest review UI.
 * Thresholds match docs/CHANGE_REQUEST_DESIGN.md §13.
 */

import type { ChangeRequestStatus } from "@/lib/api/types";

export type ConfidenceLevel = "green" | "yellow" | "red";

export function confidenceColor(c: number | undefined | null): ConfidenceLevel {
  const v = c ?? 0;
  if (v >= 0.9) return "green";
  if (v >= 0.7) return "yellow";
  return "red";
}

export function confidenceLabel(c: number | undefined | null): string {
  const v = c ?? 0;
  if (v >= 0.9) return "자동 채택 후보";
  if (v >= 0.7) return "검토 필요";
  return "운영자 입력 필수";
}

const TEXT_BY_LEVEL: Record<ConfidenceLevel, string> = {
  green: "text-emerald-700 dark:text-emerald-300",
  yellow: "text-amber-700 dark:text-amber-300",
  red: "text-rose-700 dark:text-rose-300",
};

const BG_BY_LEVEL: Record<ConfidenceLevel, string> = {
  green: "bg-emerald-500/10",
  yellow: "bg-amber-500/10",
  red: "bg-rose-500/10",
};

const BORDER_BY_LEVEL: Record<ConfidenceLevel, string> = {
  green: "border-emerald-500/40",
  yellow: "border-amber-500/40",
  red: "border-rose-500/40",
};

export function confidenceClasses(c: number | undefined | null): {
  text: string;
  bg: string;
  border: string;
  level: ConfidenceLevel;
} {
  const level = confidenceColor(c);
  return {
    level,
    text: TEXT_BY_LEVEL[level],
    bg: BG_BY_LEVEL[level],
    border: BORDER_BY_LEVEL[level],
  };
}

export function formatConfidence(c: number | undefined | null): string {
  if (c == null) return "—";
  return `${Math.round(c * 100)}%`;
}

// ---------------------------------------------------------------------------
// ChangeRequest status color mapping — kept here so the same palette feeds
// both the list page badges and the review screen header.
// ---------------------------------------------------------------------------

export type StatusBadgeVariant =
  | "muted"
  | "info"
  | "success"
  | "warning"
  | "destructive";

const STATUS_VARIANT: Record<ChangeRequestStatus, StatusBadgeVariant> = {
  draft: "muted",
  awaiting_assistant: "warning",
  assistant_responded: "info",
  issued_md: "info",
  issued_pr: "info",
  issued_issue: "info",
  merged: "success",
  rejected: "destructive",
  superseded: "warning",
};

const STATUS_LABEL: Record<ChangeRequestStatus, string> = {
  draft: "초안",
  awaiting_assistant: "Claude 응답 대기",
  assistant_responded: "검토 가능",
  issued_md: "Markdown 발행",
  issued_pr: "PR 발행",
  issued_issue: "Issue 발행",
  merged: "병합 완료",
  rejected: "거절",
  superseded: "대체됨",
};

export function statusColor(status: ChangeRequestStatus): StatusBadgeVariant {
  return STATUS_VARIANT[status] ?? "muted";
}

export function statusLabel(status: ChangeRequestStatus): string {
  return STATUS_LABEL[status] ?? status;
}

/** Average of the per-field confidence values, or null if empty. */
export function averageConfidence(
  conf: Record<string, number> | undefined | null,
): number | null {
  if (!conf) return null;
  const values = Object.values(conf).filter((v) => typeof v === "number");
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}
