/**
 * Hand-mirrored stack registry — kept in sync with config/stacks.yaml.
 *
 * The Stack Explorer on the home page reads from this list.
 * Each entry maps a HEAXHub stack key to:
 *   - a capability group (drives tab placement & color)
 *   - display label / monogram / tagline
 *   - example count (for the small badge)
 *
 * When config/stacks.yaml is updated, mirror the change here.
 */

import type { CapabilityKey } from "@/styles/tokens";

export interface StackDef {
  /** stack key from config/stacks.yaml (e.g. "fastapi") */
  key: string;
  /** human label */
  label: string;
  /** 1–3 char monogram for the card tile */
  monogram: string;
  /** <= 32 char tagline */
  tagline: string;
  /** capability bucket → drives Stack Explorer tab */
  capability: CapabilityKey;
  /** number of demo / example apps shipped under this stack */
  examples: number;
}

export const STACKS: StackDef[] = [
  // ── data dashboards ──────────────────────────────────────────────
  { key: "streamlit",        label: "Streamlit",        monogram: "St", tagline: "즉석 데이터 대시보드",      capability: "data_dash",    examples: 1 },
  { key: "dash_plotly",      label: "Plotly Dash",      monogram: "Ds", tagline: "Plotly 기반 대시보드",      capability: "data_dash",    examples: 0 },
  { key: "shiny_for_python", label: "Shiny for Python", monogram: "Sh", tagline: "reactive 분석 UI",          capability: "data_dash",    examples: 0 },

  // ── web services ────────────────────────────────────────────────
  { key: "fastapi",          label: "FastAPI",          monogram: "Fa", tagline: "Python REST 서비스",        capability: "web_service",  examples: 1 },
  { key: "flask",            label: "Flask",            monogram: "Fl", tagline: "가벼운 Python WSGI",        capability: "web_service",  examples: 0 },
  { key: "nextjs",           label: "Next.js",          monogram: "Js", tagline: "React SSR / SPA",           capability: "web_service",  examples: 1 },
  { key: "nodejs_express",   label: "Node.js Express",  monogram: "Nd", tagline: "Node REST / WebSocket",     capability: "web_service",  examples: 0 },
  { key: "go_service",       label: "Go Service",       monogram: "Go", tagline: "단일 바이너리 HTTP/gRPC",   capability: "web_service",  examples: 0 },

  // ── batch / jobs ────────────────────────────────────────────────
  { key: "python_cli",       label: "Python CLI",       monogram: "Py", tagline: "스크립트 → 출력 파일",      capability: "batch_job",    examples: 1 },
  { key: "r_script",         label: "R Script",         monogram: "R",  tagline: "통계 / 후처리 배치",        capability: "batch_job",    examples: 0 },

  // ── external integrations ───────────────────────────────────────
  { key: "external_link",    label: "External Link",    monogram: "↗",  tagline: "외부 URL 바로가기",         capability: "external_int", examples: 0 },
  { key: "external_iframe",  label: "External iFrame",  monogram: "▭",  tagline: "임베드된 외부 UI",          capability: "external_int", examples: 0 },
  { key: "external_proxy",   label: "External Proxy",   monogram: "↪",  tagline: "역프록시된 사내 서비스",    capability: "external_int", examples: 0 },

  // ── static hosting ──────────────────────────────────────────────
  { key: "static_html",      label: "Static HTML",      monogram: "{}", tagline: "정적 사이트 호스팅",        capability: "static_host",  examples: 1 },
  { key: "mkdocs_static",    label: "MkDocs",           monogram: "Md", tagline: "사전 빌드 문서 사이트",     capability: "static_host",  examples: 0 },

  // ── desktop ─────────────────────────────────────────────────────
  { key: "windows_local",    label: "Windows Local",    monogram: "Wn", tagline: ".exe 인스톨러 + 핸들러",    capability: "desktop",      examples: 0 },
];

/** Stacks grouped by capability — preserves declaration order. */
export function stacksByCapability(): Record<CapabilityKey, StackDef[]> {
  const out: Record<CapabilityKey, StackDef[]> = {
    web_service: [],
    data_dash: [],
    batch_job: [],
    external_int: [],
    static_host: [],
    desktop: [],
  };
  for (const s of STACKS) out[s.capability].push(s);
  return out;
}

export function stackCount(): number {
  return STACKS.length;
}
