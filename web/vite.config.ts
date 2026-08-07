import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["apple-touch-icon.png", "favicon-32.png"],
      manifest: {
        name: "무신사 오프라인 대시보드",
        short_name: "오프라인 대시보드",
        description: "MUSINSA 오프라인 매장 판매·재고 대시보드 (사내 전용)",
        lang: "ko",
        theme_color: "#4f46e5",
        background_color: "#0f172a",
        display: "standalone",
        orientation: "portrait",
        start_url: "/",
        scope: "/",
        icons: [
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "maskable-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      workbox: {
        // 폰트(woff2)는 프리캐시 제외 → Pretendard 다이내믹 서브셋 수백 개가 설치 시 한꺼번에
        // 받아지지 않도록. 대신 아래 runtimeCaching으로 사용된 글리프만 CacheFirst 캐시.
        globPatterns: ["**/*.{js,css,html,png,svg}"],
        maximumFileSizeToCacheInBytes: 4 * 1024 * 1024, // aggrid 청크(~1MB) 포함
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api\//], // API는 항상 네트워크(SW 미개입)
        runtimeCaching: [
          {
            urlPattern: ({ request }) => request.destination === "font",
            handler: "CacheFirst",
            options: {
              cacheName: "fonts",
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 * 365 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
  server: {
    host: true, // LAN(폰) 접속 허용
    port: 5173,
    proxy: {
      // 개발 중 /api 요청을 FastAPI(8000)로 전달 → CORS 걱정 없이 동작
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 벤더 청크 분리 → 캐싱/병렬 로드 개선 (AG Grid·Recharts가 큼)
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("ag-grid")) return "aggrid";
            if (id.includes("recharts") || id.includes("d3-")) return "charts";
            if (id.includes("react")) return "react";
            return "vendor";
          }
        },
      },
    },
  },
});
