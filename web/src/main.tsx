import React from "react";
import ReactDOM from "react-dom/client";
// 폰트 셀프호스팅(CDN 차단 환경 대비): Inter(라틴·숫자) + Pretendard(한글, 다이내믹 서브셋)
import "@fontsource-variable/inter";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "./index.css";
import App from "./App";

// 재배포로 청크 해시가 바뀌어 구버전이 옛 청크를 못 찾을 때(특히 PWA 캐시) 1회 자동 새로고침.
window.addEventListener("vite:preloadError", () => {
  if (!sessionStorage.getItem("__reloadedForChunk")) {
    sessionStorage.setItem("__reloadedForChunk", "1");
    window.location.reload();
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
