import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  preview: {
    port: 4173,
    proxy: {
      "/api": { target: process.env.VITE_DEV_API ?? "http://localhost:8000", changeOrigin: true },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_DEV_API ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
      outDir: "dist",
      chunkSizeWarningLimit: 1600,
      rollupOptions: {
        output: {
          manualChunks: (id: string) => {
            if (id.includes("/node_modules/echarts/")) return "echarts";
            if (id.includes("/node_modules/maplibre-gl/")) return "maplibre";
            if (
              id.includes("/node_modules/react/") ||
              id.includes("/node_modules/react-dom/") ||
              id.includes("/node_modules/zustand/")
            ) return "react";
            return undefined;
          },
        },
    },
  },
});
