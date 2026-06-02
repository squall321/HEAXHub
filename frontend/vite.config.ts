import path from "node:path";
import { TanStackRouterVite } from "@tanstack/router-vite-plugin";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    TanStackRouterVite({
      routesDirectory: "./src/routes",
      generatedRouteTree: "./src/routeTree.gen.ts",
      autoCodeSplitting: true,
    }),
    react(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:4040",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:4040",
        ws: true,
        changeOrigin: true,
      },
    },
  },
  build: {
    chunkSizeWarningLimit: 300,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (id.includes("/react-dom/") || /\/react\/[^/]*$/.test(id) || id.match(/node_modules\/react\//)) {
            return "react-vendor";
          }
          if (id.includes("/@tanstack/")) return "tanstack";
          if (id.includes("/@radix-ui/")) {
            if (id.includes("/react-select/")) return "radix-select";
            if (
              id.includes("/react-dialog/") ||
              id.includes("/react-dropdown-menu/") ||
              id.includes("/react-popover/") ||
              id.includes("/react-tooltip/")
            ) {
              return "radix-overlay";
            }
            return "radix-base";
          }
          if (
            id.includes("/react-hook-form/") ||
            id.includes("/@hookform/") ||
            id.includes("/zod/")
          ) {
            return "forms";
          }
          if (id.includes("/framer-motion/")) return "framer-motion";
          return undefined;
        },
      },
    },
  },
});
