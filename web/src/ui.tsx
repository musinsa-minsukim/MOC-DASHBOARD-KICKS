import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

// 컨테이너 폭을 넘으면 글자 크기를 줄여 한 줄에 맞춤(말줄임 "…" 대신). 넘지 않으면 그대로.
// 모바일 좁은 카드에서 KPI 값이 잘리지 않도록 사용. min 미만으로는 안 줄이고 그 아래는 클립.
export function FitText({ children, min = 11, className = "" }: { children: React.ReactNode; min?: number; className?: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const parent = el.parentElement; if (!parent) return;
    const fit = () => {
      el.style.fontSize = "";
      const base = parseFloat(getComputedStyle(el).fontSize) || 16;
      const avail = parent.clientWidth;
      const natural = el.scrollWidth;
      if (avail > 0 && natural > avail) el.style.fontSize = `${Math.max(min, (avail / natural) * base)}px`;
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(parent);
    return () => ro.disconnect();
  });
  return <span ref={ref} className={className} style={{ display: "inline-block", whiteSpace: "nowrap" }}>{children}</span>;
}

type Div = React.HTMLAttributes<HTMLDivElement>;

export function Card({ className = "", ...p }: Div) {
  return (
    <div
      className={`rounded-xl border border-slate-200/80 bg-white shadow-[var(--shadow-card)] transition-[box-shadow,border-color,transform] duration-200 hover:border-slate-300/90 hover:shadow-[var(--shadow-card-hover)] dark:border-slate-800 dark:bg-slate-900 dark:hover:border-slate-700 ${className}`}
      {...p}
    />
  );
}

export function CardBody({ className = "", ...p }: Div) {
  return <div className={`p-5 ${className}`} {...p} />;
}

export function SectionTitle({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="text-[15px] font-semibold tracking-tight text-slate-900 dark:text-slate-50">{title}</h3>
        {sub && <p className="mt-1 text-xs leading-relaxed text-slate-500 dark:text-slate-400">{sub}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

export function Chip({
  active,
  children,
  onClick,
}: {
  active?: boolean;
  children: React.ReactNode;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition duration-150 active:scale-[0.97] ${
        active
          ? "bg-indigo-600 text-white shadow-sm hover:bg-indigo-500"
          : "border border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:bg-slate-700"
      }`}
    >
      {children}
    </button>
  );
}

export function Button({
  className = "",
  variant = "primary",
  ...p
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition duration-150 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50";
  const styles =
    variant === "primary"
      ? "bg-indigo-600 text-white shadow-sm hover:bg-indigo-500 hover:shadow-md"
      : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800";
  return <button className={`${base} ${styles} ${className}`} {...p} />;
}

export function MultiSelect({
  label,
  options,
  value,
  onChange,
  searchable = true,
}: {
  label: string;
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
  searchable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [pinned, setPinned] = useState<string[]>([]); // 열린 시점의 선택값 → 최상단 고정(토글해도 위치 안 흔들림)
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  // 팝업 열릴 때마다 현재 선택값을 상단 고정 대상으로 스냅샷 + 검색 초기화
  useEffect(() => {
    if (open) { setPinned(value); setQ(""); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const pinnedSet = useMemo(() => new Set(pinned), [pinned]);
  const ordered = useMemo(
    () => [...options.filter((o) => pinnedSet.has(o)), ...options.filter((o) => !pinnedSet.has(o))],
    [options, pinnedSet],
  );
  const filtered = searchable && q ? ordered.filter((o) => o.toLowerCase().includes(q.toLowerCase())) : ordered;
  const pinCount = useMemo(() => filtered.reduce((n, o) => n + (pinnedSet.has(o) ? 1 : 0), 0), [filtered, pinnedSet]);
  const toggle = (o: string) => onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o]);
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition duration-150 active:scale-[0.98] ${
          value.length
            ? "border-indigo-300 bg-indigo-50 text-indigo-700 hover:bg-indigo-100 dark:border-indigo-800 dark:bg-indigo-950 dark:text-indigo-300 dark:hover:bg-indigo-900/70"
            : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:border-slate-600 dark:hover:bg-slate-700"
        }`}
      >
        <span className="max-w-[150px] truncate">{label}{value.length ? ` · ${value.length}` : ""}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" className={`shrink-0 transition ${open ? "rotate-180" : ""}`}><path d="m6 9 6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="animate-pop absolute left-0 z-30 mt-1.5 w-[min(18rem,calc(100vw-1.5rem))] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-[var(--shadow-pop)] dark:border-slate-700 dark:bg-slate-800">
          {searchable && (
            <div className="border-b border-slate-100 p-2 dark:border-slate-700">
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="검색…"
                className="w-full rounded-md border border-slate-200 px-2 py-1 text-sm outline-none transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/25 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
          )}
          <div className="flex items-center justify-between px-2 py-1 text-[11px] text-slate-400">
            <span>{value.length}개 선택 · {filtered.length}개</span>
            {value.length > 0 && (
              <button type="button" onClick={() => onChange([])} className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">전체 해제</button>
            )}
          </div>
          <div className="max-h-60 overflow-auto pb-1">
            {filtered.length === 0 && <div className="px-3 py-3 text-center text-xs text-slate-400">결과 없음</div>}
            {filtered.slice(0, 500).map((o, i) => (
              <label
                key={o}
                className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm transition-colors hover:bg-indigo-50/60 dark:hover:bg-slate-700/50 ${
                  i === pinCount && pinCount > 0 ? "mt-0.5 border-t border-slate-100 pt-2 dark:border-slate-700" : ""
                }`}
              >
                <input type="checkbox" checked={value.includes(o)} onChange={() => toggle(o)} className="accent-indigo-600" />
                <span className="truncate text-slate-700 dark:text-slate-200" title={o}>{o}</span>
              </label>
            ))}
            {filtered.length > 500 && <div className="px-3 py-2 text-center text-[11px] text-slate-400">상위 500개 표시 · 검색으로 좁히세요</div>}
          </div>
        </div>
      )}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <div
      className={`h-5 w-5 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-600 dark:border-slate-700 dark:border-t-indigo-400 ${className}`}
    />
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-slate-100 dark:bg-slate-800 ${className}`} />;
}
