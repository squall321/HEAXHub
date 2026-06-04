/**
 * HEAXHub design tokens v2 — capability-grouped stack ecosystem.
 * Use these constants in TS code (gradients, charts, motion).
 * Tailwind classes can also be used directly via tailwind.config.ts colors.
 */

export const colors = {
  brand: {
    50: "#eff6ff",
    100: "#dbeafe",
    400: "#818cf8",
    500: "#4338ca",
    600: "#3730a3",
    900: "#1e1b4b",
    950: "#020617",
  },
  accent: {
    gold: "#fcd34d",
    amber: "#d97706",
    emerald: "#10b981",
    rose: "#f43f5e",
  },

  // legacy app_type accents — kept for AppCard back-compat
  category: {
    cli_tool: "#0891b2",
    web_app: "#16a34a",
    windows_gui: "#7c3aed",
    remote_app: "#0d9488",
    external_link: "#64748b",
    slurm_job: "#d97706",
    container_app: "#db2777",
  },

  // capability-level grouping for Stack Explorer
  capability: {
    web_service: { base: "#10b981", from: "#064e3b", to: "#10b981" }, // emerald
    data_dash: { base: "#0ea5e9", from: "#0c4a6e", to: "#0ea5e9" }, // sky
    batch_job: { base: "#f59e0b", from: "#7c2d12", to: "#f59e0b" }, // amber
    external_int: { base: "#64748b", from: "#1e293b", to: "#64748b" }, // slate
    static_host: { base: "#a855f7", from: "#3b0764", to: "#a855f7" }, // violet
    desktop: { base: "#f43f5e", from: "#4c0519", to: "#f43f5e" }, // rose
  },
} as const;

export const categoryHue = colors.category;
export type AppCategory = keyof typeof colors.category;

export const categoryLabel: Record<AppCategory, string> = {
  cli_tool: "CLI Tool",
  web_app: "Web App",
  windows_gui: "Windows GUI",
  remote_app: "Remote App",
  external_link: "External Link",
  slurm_job: "Slurm Job",
  container_app: "Container",
};

// Stack Explorer — capability-level grouping (separate from app_type).
// Mirrors `colors.capability` for direct import compatibility.
export const capability = colors.capability;
export type CapabilityKey = keyof typeof colors.capability;

export const capabilityLabel: Record<CapabilityKey, string> = {
  web_service: "Web 서비스",
  data_dash: "데이터 대시보드",
  batch_job: "잡 / 배치",
  external_int: "외부 통합",
  static_host: "정적 호스팅",
  desktop: "데스크톱",
};

// Stack registry mirrors config/stacks.yaml. monogram drives StackCard.
export interface StackDef {
  key: string;
  label: string;
  monogram: string;
  tagline: string;
  capability: CapabilityKey;
}

export const STACKS: StackDef[] = [
  { key: "streamlit", label: "Streamlit", monogram: "St", tagline: "즉석 데이터 대시보드", capability: "data_dash" },
  { key: "dash", label: "Plotly Dash", monogram: "Ds", tagline: "Plotly 기반 대시보드", capability: "data_dash" },
  { key: "shiny", label: "R Shiny", monogram: "Sh", tagline: "통계 분석 UI", capability: "data_dash" },

  { key: "fastapi", label: "FastAPI", monogram: "Fa", tagline: "Python REST 서비스", capability: "web_service" },
  { key: "flask", label: "Flask", monogram: "Fl", tagline: "가벼운 Python 웹앱", capability: "web_service" },
  { key: "nextjs", label: "Next.js", monogram: "Js", tagline: "React SSR / SPA", capability: "web_service" },
  { key: "go", label: "Go HTTP", monogram: "Go", tagline: "단일 바이너리 서비스", capability: "web_service" },
  { key: "java", label: "Java", monogram: "Jv", tagline: "JVM 기반 서비스", capability: "web_service" },
  { key: "dotnet", label: ".NET", monogram: ".N", tagline: "ASP.NET / Kestrel", capability: "web_service" },
  { key: "rust", label: "Rust", monogram: "Rs", tagline: "axum / actix 서비스", capability: "web_service" },

  { key: "python_cli", label: "Python CLI", monogram: "Py", tagline: "스크립트 → 결과 파일", capability: "batch_job" },
  { key: "cpp", label: "C++ 바이너리", monogram: "C+", tagline: "네이티브 솔버 / CAE", capability: "batch_job" },
  { key: "r", label: "R 스크립트", monogram: "R", tagline: "통계 / 후처리 배치", capability: "batch_job" },
  { key: "apptainer_sif", label: "Apptainer SIF", monogram: "Ap", tagline: "HPC 컨테이너 잡", capability: "batch_job" },

  { key: "external_link", label: "External Link", monogram: "→", tagline: "외부 URL 바로가기", capability: "external_int" },
  { key: "external_iframe", label: "External iFrame", monogram: "▭", tagline: "임베드된 외부 UI", capability: "external_int" },
  { key: "external_proxy", label: "External Proxy", monogram: "↪", tagline: "역프록시된 사내 서비스", capability: "external_int" },

  { key: "static_html", label: "Static HTML", monogram: "{}", tagline: "정적 사이트 호스팅", capability: "static_host" },
  { key: "mkdocs_static", label: "MkDocs", monogram: "Md", tagline: "문서 사이트 빌드", capability: "static_host" },

  { key: "windows_local", label: "Windows Local", monogram: "Wn", tagline: ".exe 인스톨러 + 핸들러", capability: "desktop" },
];

// Refined gradients for hero & aurora layers
export const heroGradient =
  "linear-gradient(140deg,#020617 0%,#1e1b4b 38%,#3730a3 72%,#4f46e5 100%)";
export const heroAuroraA =
  "radial-gradient(60% 50% at 18% 22%, rgba(252,211,77,0.18), transparent 70%)";
export const heroAuroraB =
  "radial-gradient(55% 45% at 82% 30%, rgba(129,140,248,0.28), transparent 75%)";
export const heroAuroraC =
  "radial-gradient(70% 60% at 50% 110%, rgba(16,185,129,0.18), transparent 70%)";
export const conceptGradient = "linear-gradient(135deg,#020617,#1e1b4b)";

export const capabilityGradient = (k: CapabilityKey) =>
  `linear-gradient(135deg, ${colors.capability[k].from} 0%, ${colors.capability[k].to} 100%)`;

// Motion timing helpers — single source of truth for animation durations.
export const motionTiming = {
  fast: 0.18,
  base: 0.24,
  smooth: 0.4,
  hero: 0.55,
  counter: 1.1,
  brandLogo: 1.6,
  auroraDriftSec: 18,
} as const;

export const motionEase = {
  /** ease-out spring-like cubic-bezier */
  standard: [0.22, 1, 0.36, 1] as const,
  easeOut: "easeOut" as const,
};
