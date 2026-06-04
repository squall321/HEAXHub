import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

type HealthState = "ok" | "degraded" | "unknown";

interface HealthSnapshot {
  state: HealthState;
  label: string;
}

const VERSION = "v0.1.0";
const GIT_SHA: string | undefined = import.meta.env.VITE_GIT_SHA;

/**
 * Bottom-of-page footer.
 *
 * - 3-column layout on md+: brand, docs, contact.
 * - One-shot /health probe on mount drives the status pill.
 * - Build version + (optional) short git SHA in the bottom bar.
 */
export function Footer() {
  const health = useHealth();

  return (
    <footer className="mt-16 border-t bg-card/40">
      <div className="mx-auto grid w-full max-w-7xl gap-8 px-6 py-10 md:grid-cols-3 md:px-10">
        {/* Brand + status */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold tracking-tight text-foreground">
              HEAXHub
            </span>
            <span className="text-xs text-muted-foreground">사내 자동화 통합 포탈</span>
          </div>
          <StatusPill health={health} />
          <p className="max-w-xs text-xs text-muted-foreground">
            CAE · 데이터 분석 · GUI 도구를 한 곳에서 신청·승인·빌드·공개.
          </p>
        </div>

        {/* Docs */}
        <div className="space-y-2">
          <h3 className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
            문서
          </h3>
          <ul className="space-y-1.5 text-sm">
            <FooterLink to="/apps">앱 카탈로그</FooterLink>
            <FooterLink to="/submit">새 앱 신청</FooterLink>
            <FooterLink to="/jobs">실행 이력</FooterLink>
            <li>
              <a
                href="/docs"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                시작 가이드
              </a>
            </li>
          </ul>
        </div>

        {/* Contact */}
        <div className="space-y-2">
          <h3 className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">
            연락
          </h3>
          <ul className="space-y-1.5 text-sm">
            <li>
              <a
                href="mailto:cae-automation@company.com"
                className="text-muted-foreground transition-colors hover:text-foreground"
              >
                cae-automation@company.com
              </a>
            </li>
            <li className="text-xs text-muted-foreground">
              #heaxhub (사내 Slack)
            </li>
          </ul>
        </div>
      </div>

      {/* Version footer */}
      <div className="border-t border-border/60">
        <div className="mx-auto flex max-w-7xl flex-col gap-1 px-6 py-3 text-[11px] text-muted-foreground md:flex-row md:items-center md:justify-between md:px-10">
          <div className="flex items-center gap-2">
            <span className="font-mono">{VERSION}</span>
            {GIT_SHA && (
              <>
                <span className="text-muted-foreground/50">·</span>
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                  {GIT_SHA.slice(0, 7)}
                </code>
              </>
            )}
          </div>
          <div className="text-muted-foreground/70">
            © {new Date().getFullYear()} HEAXHub
          </div>
        </div>
      </div>
    </footer>
  );
}

function FooterLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <li>
      <Link
        to={to}
        className="text-muted-foreground transition-colors hover:text-foreground"
      >
        {children}
      </Link>
    </li>
  );
}

function StatusPill({ health }: { health: HealthSnapshot }) {
  const palette: Record<HealthState, { bg: string; text: string; dot: string }> = {
    ok: {
      bg: "bg-emerald-500/15",
      text: "text-emerald-600 dark:text-emerald-400",
      dot: "bg-emerald-500",
    },
    degraded: {
      bg: "bg-amber-500/15",
      text: "text-amber-600 dark:text-amber-400",
      dot: "bg-amber-500",
    },
    unknown: {
      bg: "bg-muted",
      text: "text-muted-foreground",
      dot: "bg-muted-foreground",
    },
  };
  const p = palette[health.state];
  return (
    <span
      className={
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-bold " +
        `${p.bg} ${p.text}`
      }
      aria-label={`서비스 상태: ${health.label}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${p.dot}`} aria-hidden />
      {health.label}
    </span>
  );
}

/**
 * One-shot fetch to /health. We intentionally do NOT poll — the StatusStrip
 * handles live counts. This is a low-noise badge.
 */
function useHealth(): HealthSnapshot {
  const [health, setHealth] = useState<HealthSnapshot>({
    state: "unknown",
    label: "상태 확인 중",
  });

  useEffect(() => {
    let cancelled = false;
    const base = import.meta.env.VITE_API_BASE ?? "/api/v1";
    // Backend exposes /health at the app root (not under /api/v1). Try both.
    const candidates = ["/health", `${base}/health`];

    (async () => {
      for (const url of candidates) {
        try {
          const res = await fetch(url, { credentials: "omit" });
          if (cancelled) return;
          if (res.ok) {
            setHealth({ state: "ok", label: "정상 운영" });
            return;
          }
        } catch {
          /* try next */
        }
      }
      if (!cancelled) setHealth({ state: "degraded", label: "상태 알 수 없음" });
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return health;
}
