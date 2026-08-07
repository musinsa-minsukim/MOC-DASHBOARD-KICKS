// API 클라이언트 + 포맷 유틸

const TOKEN_KEY = "dash_token";
const NAME_KEY = "dash_name";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const getName = () => localStorage.getItem(NAME_KEY) || "";
export function setAuth(token: string | null, name = "") {
  if (token) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(NAME_KEY, name);
  } else {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(NAME_KEY);
  }
}

async function req(path: string, opts: RequestInit = {}) {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  const tok = getToken();
  if (tok) headers["Authorization"] = `Bearer ${tok}`;
  if (opts.body) headers["Content-Type"] = "application/json";
  const r = await fetch("/api" + path, { ...opts, headers });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = (await r.json()).detail || detail;
    } catch {}
    if (r.status === 401 && tok) {
      setAuth(null); // 만료된 토큰 → 정리
      window.dispatchEvent(new Event("auth-expired"));
    }
    throw new Error(detail);
  }
  return r.json();
}

export type Filters = {
  date_from: string;
  date_to: string;
  biz: string[];
  type: string[];
  store: string[];
  brand: string[];
  cat_top: string[];
  cat_large: string[];
  cat_medium: string[];
  md: string[];
  goods: string; // UID 다중 입력(원문) — 쉼표/공백/줄바꿈 구분
  gran: "day" | "week" | "month";
};

// UID 원문 → 정수 배열
export function parseUids(raw: string): number[] {
  return (raw || "")
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter((s) => /^\d+$/.test(s))
    .map(Number);
}

// 공통 데이터 필터 → 쿼리스트링 (date 제외 옵션: 비교 탭은 기준일 윈도우 사용)
export function toQuery(f: Filters, opts: { withDate?: boolean } = {}): string {
  const withDate = opts.withDate !== false;
  const p = new URLSearchParams();
  if (withDate && f.date_from) p.append("date_from", f.date_from);
  if (withDate && f.date_to) p.append("date_to", f.date_to);
  const multi = (k: string, arr: string[]) => arr.forEach((v) => p.append(k, v));
  multi("biz", f.biz);
  multi("type", f.type);
  multi("store", f.store);
  multi("brand", f.brand);
  multi("cat_top", f.cat_top);
  multi("cat_large", f.cat_large);
  multi("cat_medium", f.cat_medium);
  multi("md", f.md);
  parseUids(f.goods).forEach((g) => p.append("goods", String(g)));
  return p.toString() ? "?" + p.toString() : "";
}

// 빈 필터 기본값
export function emptyFilters(dateFrom: string, dateTo: string): Filters {
  return { date_from: dateFrom, date_to: dateTo, biz: [], type: [], store: [], brand: [],
    cat_top: [], cat_large: [], cat_medium: [], md: [], goods: "", gran: "day" };
}

export const api = {
  login: (username: string, password: string) =>
    req("/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  meta: () => req("/meta"),
  status: () => req("/status"),
  refreshSales: () => req("/refresh/sales", { method: "POST" }),
  daily: (qs = "") => req("/daily" + qs),
  summary: (qs: string) => req("/summary" + qs),
  aov: (qs: string) => req("/aov" + qs),
  hourly: (qs: string) => req("/hourly" + qs),
  trend: (qs: string) => req("/trend" + qs),
  by: (dim: string, qs: string, limit = 100) =>
    req(`/by/${dim}` + qs + (qs ? "&" : "?") + `limit=${limit}`),
  drill: (level: string, qs: string, limit = 1000) =>
    req("/drill" + qs + (qs ? "&" : "?") + `level=${level}&limit=${limit}`),
  customer: (qs: string) => req("/customer" + qs),
  footfall: (qs: string) => req("/footfall" + qs),
  customerCountry: (qs: string, limit = 20) =>
    req("/customer/country" + qs + (qs ? "&" : "?") + `limit=${limit}`),
  inventory: (qs: string) => req("/inventory" + qs),
  compare: (qs: string) => req("/compare" + qs),
  target: (qs = "") => req("/target" + qs),
  salesBrands: (qs: string) => req("/sales/brands" + qs),
  salesGoods: (qs: string, limit = 1500) =>
    req("/sales/goods" + qs + (qs ? "&" : "?") + `limit=${limit}`),
};

// ---- 포맷 ----
export const num = (n: number) => Math.round(n || 0).toLocaleString("ko-KR");
export const won = (n: number) => num(n) + "원";
export const pct = (n: number) => (n > 0 ? "+" : "") + (n || 0).toFixed(1) + "%";
export function compact(n: number): string {
  const a = Math.abs(n || 0);
  if (a >= 1e8) return (n / 1e8).toFixed(1) + "억";
  if (a >= 1e4) return (n / 1e4).toFixed(0) + "만";
  return num(n);
}
export const todayISO = () => {
  const t = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}`;
};
// ISO 날짜 문자열에서 d일 전 (UTC 안 거치고 로컬 계산 → 자정 경계 off-by-one 방지)
export function daysBeforeISO(iso: string, d: number) {
  const [y, m, day] = iso.slice(0, 10).split("-").map(Number);
  const t = new Date(y, m - 1, day);
  t.setDate(t.getDate() - d);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())}`;
}
// 직전 동기간 [prev_from, prev_to]: 기간 길이만큼 바로 앞 구간 (신장율 비교 기준)
export function prevRange(from: string, to: string) {
  const days = (Date.parse(to.slice(0, 10)) - Date.parse(from.slice(0, 10))) / 86400000 + 1;
  return { from: daysBeforeISO(from, days), to: daysBeforeISO(from, 1) };
}
