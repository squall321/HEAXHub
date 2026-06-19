import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" → 모든 번들된 자산 URL 이 상대경로로 생성된다.
// 덕분에 Caddy 가 /apps/<slug>/ 서브패스로 마운트해도 자산 경로가 깨지지 않는다.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
