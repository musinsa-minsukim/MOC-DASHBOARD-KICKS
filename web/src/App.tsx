import { useEffect, useState, lazy, Suspense } from "react";
import {
  LayoutDashboard,
  ShoppingCart,
  Users,
  BarChart3,
  Package,
  Target as TargetIcon,
  ListTree,
  Coins,
  LogOut,
  RefreshCw,
  BarChart4,
  Sun,
  Moon,
} from "lucide-react";
import { api, getName, getToken, setAuth, emptyFilters, daysBeforeISO, todayISO, type Filters } from "./lib";
import { Spinner } from "./ui";
import Login from "./Login";
import FilterBar from "./FilterBar";
const Dashboard = lazy(() => import("./Dashboard"));
const Summary = lazy(() => import("./Summary"));
const Customer = lazy(() => import("./Customer"));
const Inventory = lazy(() => import("./Inventory"));
const Compare = lazy(() => import("./Compare"));
const Target = lazy(() => import("./Target"));
const Drill = lazy(() => import("./Drill"));
const Pnl = lazy(() => import("./Pnl"));

const NAV = [
  { key: "summary", label: "요약", icon: LayoutDashboard, ready: true },
  { key: "sales", label: "판매", icon: ShoppingCart, ready: true },
  { key: "pnl", label: "손익", icon: Coins, ready: true },
  { key: "drill", label: "드릴다운", icon: ListTree, ready: true },
  { key: "target", label: "목표 대비 실적", icon: TargetIcon, ready: true },
  { key: "customer", label: "고객·외국인", icon: Users, ready: true },
  { key: "compare", label: "비교·신장율", icon: BarChart3, ready: true },
  { key: "inventory", label: "재고", icon: Package, ready: true },
];

function useDarkMode(): [boolean, () => void] {
  const [dark, setDark] = useState(() => {
    const s = localStorage.getItem("theme");
    if (s) return s === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("theme", dark ? "dark" : "light");
  }, [dark]);
  return [dark, () => setDark((d) => !d)];
}

function Sidebar({ view, setView }: { view: string; setView: (k: string) => void }) {
  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col self-start overflow-y-auto border-r border-slate-200 bg-white lg:flex dark:border-slate-800 dark:bg-slate-900">
      <div className="flex h-16 items-center gap-2.5 border-b border-slate-100 px-5 dark:border-slate-800">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-indigo-600 text-white shadow-sm shadow-indigo-600/25">
          <BarChart4 size={18} />
        </div>
        <span className="font-semibold tracking-tight text-slate-900 dark:text-slate-50">오프라인 대시보드</span>
      </div>
      <nav className="flex-1 space-y-1 p-3">
        {NAV.map((n) => {
          const active = view === n.key;
          return (
            <button
              key={n.key}
              disabled={!n.ready}
              onClick={() => n.ready && setView(n.key)}
              className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors duration-150 ${
                active
                  ? "bg-indigo-50 font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-100 dark:bg-indigo-950/70 dark:text-indigo-300 dark:ring-indigo-900/60"
                  : n.ready
                    ? "font-medium text-slate-600 hover:bg-slate-100/70 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100"
                    : "cursor-not-allowed font-medium text-slate-400 dark:text-slate-600"
              }`}
              title={n.ready ? "" : "준비 중 (다음 단계)"}
            >
              <n.icon size={18} />
              {n.label}
              {!n.ready && <span className="ml-auto text-[10px] text-slate-300 dark:text-slate-700">soon</span>}
            </button>
          );
        })}
      </nav>
      <div className="border-t border-slate-100 p-4 text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
        MUSINSA · 사내 전용
      </div>
    </aside>
  );
}

function fmtDT(s?: string | null) {
  if (!s) return "—";
  const [d, t] = String(s).split("T");
  const md = (d || "").slice(5);
  return t ? `${md} ${t}` : md;
}
// 원천 timestamp "2026-06-26 14:32:11" → "06-26 14:32"
function fmtTs(s?: string | null) {
  if (!s) return "—";
  return String(s).slice(5, 16).replace("T", " ");
}

function Topbar({ title, dark, onTheme, status }: {
  title: string; dark: boolean; onTheme: () => void;
  status: any;
}) {
  return (
    <header className="sticky top-0 z-10 flex min-h-[4rem] items-center gap-3 border-b border-slate-200/70 bg-white/70 px-5 pt-[env(safe-area-inset-top)] backdrop-blur-md dark:border-slate-800/80 dark:bg-slate-900/70">
      <div className="flex items-center gap-2 lg:hidden">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-indigo-600 text-white shadow-sm shadow-indigo-600/25">
          <BarChart4 size={16} />
        </div>
      </div>
      <h2 className="text-base font-semibold tracking-tight text-slate-900 dark:text-slate-50">{title}</h2>
      <div className="ml-auto flex items-center gap-2">
        {/* 데이터 신선도 — 판매(증분)·재고(스냅샷) 기준일/갱신 */}
        <div className="hidden flex-col text-[11px] leading-tight text-slate-400 lg:flex dark:text-slate-500" title="판매=MOSS 증분(준실시간) · 재고=일별 스냅샷">
          <span>판매 데이터 <b className="font-semibold text-slate-600 dark:text-slate-300">{status?.sales_max_date ?? "—"}</b> · 최근거래 {fmtTs(status?.sales_max_ts)} · 갱신 {fmtDT(status?.sales_refreshed_at)}</span>
          <span>재고 데이터 <b className="font-semibold text-slate-600 dark:text-slate-300">{status?.inventory_pivot_data_date ?? "—"}</b> · 갱신 {fmtDT(status?.inventory_pivot_refreshed_at)}</span>
          <span className="text-amber-500 dark:text-amber-400/90">CP·입객은 익일 반영 · 순이익 오늘자는 잠정 추정 (원천 1일 지연)</span>
        </div>
        <button
          onClick={onTheme}
          title={dark ? "라이트 모드" : "다크 모드"}
          className="rounded-lg p-2 text-slate-400 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-600 active:scale-95 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          {dark ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        <div className="flex items-center gap-2 rounded-lg bg-slate-100/70 py-1 pl-2 pr-1 dark:bg-slate-800">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-indigo-600 text-xs font-semibold text-white shadow-sm shadow-indigo-600/20">
            {(getName() || "U").slice(0, 1)}
          </div>
          <span className="hidden text-sm font-medium text-slate-700 sm:block dark:text-slate-200">
            {getName() || "사용자"}
          </span>
          <button
            onClick={() => {
              setAuth(null);
              window.dispatchEvent(new Event("auth-expired"));
            }}
            title="로그아웃"
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:hover:bg-slate-700 dark:hover:text-slate-200"
          >
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </header>
  );
}

function MobileNav({ view, setView }: { view: string; setView: (k: string) => void }) {
  return (
    <nav className="flex items-center gap-1 overflow-x-auto border-b border-slate-200 bg-white px-3 py-2 lg:hidden dark:border-slate-800 dark:bg-slate-900">
      {NAV.filter((n) => n.ready).map((n) => (
        <button
          key={n.key}
          onClick={() => setView(n.key)}
          className={`flex shrink-0 items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors duration-150 ${
            view === n.key
              ? "bg-indigo-50 text-indigo-700 ring-1 ring-inset ring-indigo-100 dark:bg-indigo-950/70 dark:text-indigo-300 dark:ring-indigo-900/60"
              : "text-slate-500 hover:bg-slate-100/70 hover:text-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
          }`}
        >
          <n.icon size={15} />
          {n.label}
        </button>
      ))}
    </nav>
  );
}

export default function App() {
  const [dark, toggleDark] = useDarkMode();
  const [authed, setAuthed] = useState(!!getToken());
  const [meta, setMeta] = useState<any>(null);
  const [err, setErr] = useState("");
  const [view, setView] = useState("summary");
  const [filters, setFilters] = useState<Filters>(() => emptyFilters("", todayISO()));
  const [status, setStatus] = useState<any>(null);
  const [dataVersion] = useState(0); // 활성 탭 remount용 key (자동 갱신은 Cloud Scheduler가 수행)

  useEffect(() => {
    const onExpire = () => {
      setAuthed(false);
      setMeta(null);
    };
    window.addEventListener("auth-expired", onExpire);
    return () => window.removeEventListener("auth-expired", onExpire);
  }, []);

  // 데이터 신선도 상태 — 1분마다 + 창 포커스 시 재조회(Scheduler가 갱신한 최신 갱신시각 반영).
  // (기존엔 로그인 시 1회만 불러 세션 유지 중 갱신시각이 고정되던 문제 수정)
  useEffect(() => {
    if (!authed) return;
    const load = () => api.status().then(setStatus).catch(() => {});
    load();
    const id = window.setInterval(load, 60_000);
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [authed, dataVersion]);

  // meta 로드되면 기본 기간 = 최신 데이터일(오늘) 단일일 (시작=종료=오늘)
  useEffect(() => {
    if (meta?.date_max && !filters.date_from) {
      const today = meta.date_max.slice(0, 10);
      setFilters((f) => ({ ...f, date_from: today, date_to: today }));
    }
  }, [meta]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (authed && !meta) {
      api
        .meta()
        .then(setMeta)
        .catch((e) => setErr(e.message));
    }
  }, [authed, meta]);

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  if (!meta)
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-slate-400 dark:text-slate-400">
        <Spinner className="h-8 w-8" />
        <span className="text-sm">데이터 불러오는 중…</span>
        {err && <span className="text-xs text-rose-500">{err}</span>}
      </div>
    );

  // BI 크로스필터: 차트 요소 클릭 → 해당 차원 필터 토글(전 탭·FilterBar 칩과 공유)
  const crossFilter = (key: keyof Filters, value: string) =>
    setFilters((prev) => {
      const cur = ((prev[key] as unknown as string[]) || []);
      const has = cur.includes(value);
      return { ...prev, [key]: has ? cur.filter((x) => x !== value) : [...cur, value] } as Filters;
    });

  const navLabel = NAV.find((n) => n.key === view)?.label ?? "요약";

  return (
    <div className="flex min-h-screen">
      <Sidebar view={view} setView={setView} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar title={navLabel} dark={dark} onTheme={toggleDark} status={status} />
        <MobileNav view={view} setView={setView} />
        <main className="animate-rise mx-auto w-full max-w-[1440px] flex-1 space-y-5 p-4 md:p-6">
          {view !== "summary" && view !== "target" && (
            <div
              className="sticky z-20 -mx-4 -mt-4 mb-1 px-4 pt-4 pb-2 md:-mx-6 md:-mt-6 md:px-6 md:pt-6"
              style={{ backgroundColor: "var(--bg)", top: "calc(4rem + env(safe-area-inset-top))" }}
            >
              <FilterBar
                meta={meta}
                f={filters}
                setF={setFilters}
                showDate={view === "sales" || view === "customer" || view === "drill"}
                showGran={view === "sales" || view === "customer"}
              />
            </div>
          )}
          <Suspense fallback={<div className="flex h-64 items-center justify-center"><Spinner className="h-7 w-7" /></div>}>
            {view === "summary" && <Summary key={dataVersion} meta={meta} dark={dark} />}
            {view === "sales" && <Dashboard key={dataVersion} meta={meta} dark={dark} filters={filters} onPick={crossFilter} />}
            {view === "drill" && <Drill key={dataVersion} meta={meta} dark={dark} filters={filters} onPick={crossFilter} />}
            {view === "customer" && <Customer key={dataVersion} meta={meta} dark={dark} filters={filters} onPick={crossFilter} />}
            {view === "inventory" && <Inventory key={dataVersion} meta={meta} dark={dark} filters={filters} onPick={crossFilter} />}
            {view === "compare" && <Compare key={dataVersion} meta={meta} filters={filters} dark={dark} onPick={crossFilter} />}
            {view === "target" && <Target key={dataVersion} meta={meta} dark={dark} />}
            {view === "pnl" && <Pnl key={dataVersion} meta={meta} dark={dark} />}
          </Suspense>
        </main>
      </div>
    </div>
  );
}
