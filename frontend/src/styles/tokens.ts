/**
 * HEAXHub design tokens (mirror of PROJECT_PLAN §8.2)
 * Use these constants in TS code (gradients, charts).
 * Tailwind classes can also be used directly via tailwind.config.ts colors.
 */

export const colors = {
  brand: { 50: "#eff6ff", 500: "#4338ca", 900: "#1e1b4b", 950: "#020617" },
  accent: { gold: "#fcd34d", amber: "#d97706" },
  category: {
    cli_tool: "#0891b2",
    web_app: "#16a34a",
    windows_gui: "#7c3aed",
    remote_app: "#0d9488",
    external_link: "#64748b",
    slurm_job: "#d97706",
    container_app: "#db2777",
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

export const heroGradient = "linear-gradient(140deg,#020617 0%,#1e1b4b 40%,#4338ca 100%)";
export const conceptGradient = "linear-gradient(135deg,#020617,#1e1b4b)";
