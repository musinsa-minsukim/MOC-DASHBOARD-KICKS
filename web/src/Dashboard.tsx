import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  Legend,
  LabelList,
  ComposedChart,
  Line,
} from "recharts";
import {
  TrendingUp,
  TrendingDown,
  ShoppingBag,
  Globe,
  Percent,
  Boxes,
  Wallet,
  Tag,
  Download,
  Receipt,
  Footprints,
} from "lucide-react";
import { api, toQuery, won, num, compact, prevRange, getToken, type Filters } from "./lib";
import { Card, CardBody, SectionTitle, Spinner, FitText, Chip } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";
import { HolidayTick } from "./holidays";
import { trendLabel, pieLabel, CatTick } from "./chartlabels";

const pct0 = (p: any) => ((p.value ?? 0) as number).toFixed(0) + "%";
const pct1 = (p: any) => ((p.value ?? 0) as number).toFixed(1) + "%";
const BRAND_COLS = [
  colText("business_type", "사업구분", { minWidth: 86 }),
  colText("brand_nm", "브랜드", { pinned: "left", minWidth: 150 }),
  colNum("goods", "상품수", "int", { minWidth: 80 }),
  colNum("qty", "순판매", "num"),
  colNum("gmv", "GMV", "compact"),
  colNum("normal_amt", "정상가매출", "compact"),
  colNum("pay", "실결제", "compact"),
  colNum("net_take", "순이익(NetTake)", "compact", { minWidth: 108, headerTooltip: "순이익 = 정산 profit · 오늘자는 take rate로 잠정 추정 · 미커버 브랜드 공란 · CP는 손익 탭" }),
  colNum("foreign_gmv", "외국인GMV", "compact"),
  colNum("foreign_ratio", "외국인비중", "num", { valueFormatter: pct0 }),
  colNum("discount_rate", "할인율", "num", { valueFormatter: pct1 }),
];
const GOODS_COLS = [
  colText("brand_nm", "브랜드", { pinned: "left", minWidth: 120 }),
  colText("goods_nm", "상품", { pinned: "left", minWidth: 240 }),
  colNum("goods_no", "UID", "int", { minWidth: 96, valueFormatter: (p: any) => String(p.value ?? "") }),
  colText("style_no", "스타일넘버", { minWidth: 120 }),
  colNum("normal_price", "정상가", "num", { minWidth: 88 }),
  colNum("sale_price", "판매가(온라인)", "num", { minWidth: 100, headerTooltip: "온라인 1차세일가 (goods_sale_price_changes) · 이력 없으면 bizest→정상가" }),
  colNum("sale_unit", "실판매가", "num", { minWidth: 92, headerTooltip: "실판매단가 = GMV ÷ 순판매수량 (오프라인 실제 팔린 평균 단가)" }),
  colText("business_type", "사업구분", { minWidth: 84 }),
  colText("cat_top", "최상위", { minWidth: 88 }),
  colText("cat_large", "대카테", { minWidth: 100 }),
  colText("cat_medium", "중카테", { minWidth: 100 }),
  colNum("qty", "순판매", "num"),
  colNum("gmv", "GMV", "compact"),
  colNum("normal_amt", "정상가매출", "compact"),
  colNum("pay", "실결제", "compact"),
  colNum("net_take", "순이익(NetTake)", "compact", { minWidth: 108, headerTooltip: "순이익 = 정산 profit · 오늘자는 상품 take rate로 잠정 추정 · 미커버 상품 공란 · CP는 손익 탭" }),
  colNum("foreign_gmv", "외국인GMV", "compact"),
  colNum("foreign_ratio", "외국인비중", "num", { valueFormatter: pct0 }),
  colNum("discount_rate", "할인율", "num", { valueFormatter: pct1 }),
  colNum("op_stores", "운영중매장수", "int", { minWidth: 104, headerTooltip: "점재고 1개 이상 보유 매장 수(운영중) · 매장별 상세는 CSV" }),
  colNum("jaego", "점재고합계", "num"),
  colNum("hub", "허브합계", "num"),
];

// ── 합계행(__muTotal)·순이익률 색상 헬퍼 ──
const _sum = (rows: any[], k: string) => rows.reduce((s, r) => s + (Number(r[k]) || 0), 0);
// 커버된 순이익률 = Σ순이익(있는 행)/ΣGMV(있는 행). 색상 기준(평균)·합계행 값 공용.
function coveredNtRate(rows: any[]): number {
  let nt = 0, g = 0;
  for (const r of rows) if (!r.__muTotal && r.net_take != null) { nt += Number(r.net_take) || 0; g += Number(r.gmv) || 0; }
  return g ? (nt / g) * 100 : 0;
}
// 순이익률 색상열: 평균 대비 발산(높으면 초록 → 낮으면 노랑·주황·빨강)
function ntRateCol(rows: any[], dark: boolean) {
  const avg = coveredNtRate(rows);
  const vals = rows.filter((r) => !r.__muTotal && r.net_take_rate != null).map((r) => r.net_take_rate as number);
  const up = Math.max(1e-6, (vals.length ? Math.max(...vals) : avg + 1) - avg);
  const dn = Math.max(1e-6, avg - (vals.length ? Math.min(...vals) : avg - 1));
  return colNum("net_take_rate", "순이익률", "num", {
    minWidth: 92,
    headerTooltip: "순이익률 = 순이익 ÷ GMV · 평균 대비 색상(높을수록 초록, 낮을수록 빨강)",
    valueFormatter: (p: any) => (p.value == null ? "" : (p.value as number).toFixed(1) + "%"),
    cellStyle: (p: any) => {
      const v = p.value as number | null;
      if (v == null) return { textAlign: "right", color: "var(--ratio-neutral)" };
      let hue: number, t: number;
      if (v >= avg) { t = Math.min(1, (v - avg) / up); hue = 95 + 40 * t; }   // 연두→초록
      else { t = Math.min(1, (avg - v) / dn); hue = 55 - 55 * t; }            // 노랑→빨강
      const [base, scale] = dark ? [0.18, 0.42] : [0.12, 0.4];
      return { textAlign: "right", backgroundColor: `hsla(${hue.toFixed(0)},78%,50%,${(base + scale * t).toFixed(3)})`, fontWeight: 600, ...(dark ? { color: "#e2e8f0" } : {}) };
    },
  });
}
const _ntTotal = (rows: any[]) => (rows.some((r) => r.net_take != null) ? _sum(rows, "net_take") : null);
function brandTotalRow(rows: any[]) {
  const gmv = _sum(rows, "gmv"), normal = _sum(rows, "normal_amt"), fgn = _sum(rows, "foreign_gmv");
  return {
    __muTotal: true, business_type: "", brand_nm: "합계", goods: _sum(rows, "goods"),
    qty: _sum(rows, "qty"), gmv, gmv_ratio: 100, normal_amt: normal, pay: _sum(rows, "pay"),
    foreign_gmv: fgn, net_take: _ntTotal(rows), net_take_rate: coveredNtRate(rows),
    discount_rate: normal ? (1 - gmv / normal) * 100 : 0, foreign_ratio: gmv ? (fgn / gmv) * 100 : 0,
  };
}
function goodsTotalRow(rows: any[]) {
  const gmv = _sum(rows, "gmv"), normal = _sum(rows, "normal_amt"), fgn = _sum(rows, "foreign_gmv"), qty = _sum(rows, "qty");
  return {
    __muTotal: true, brand_nm: "합계", goods_nm: "", qty, gmv, normal_amt: normal, pay: _sum(rows, "pay"),
    foreign_gmv: fgn, net_take: _ntTotal(rows), net_take_rate: coveredNtRate(rows),
    sale_unit: qty ? gmv / qty : 0, discount_rate: normal ? (1 - gmv / normal) * 100 : 0,
    foreign_ratio: gmv ? (fgn / gmv) * 100 : 0, jaego: _sum(rows, "jaego"), hub: _sum(rows, "hub"),
  };
}

type Meta = {
  date_min: string;
  date_max: string;
  shop_types: string[];
  business_types: string[];
  stores: { store_name: string; shop_type: string }[];
};
type Summary = {
  gmv: number; qty: number; normal_amt: number; pay: number; foreign_gmv: number;
  goods_count: number; store_count: number; discount_rate: number; foreign_ratio: number;
  net_take?: number; cp?: number; cp_rate?: number;   // 순이익(Net Take)·공헌이익(CP)·CP율 (settlement 캐시 있을 때만)
};

const PIE = ["#4f46e5", "#7c3aed", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#14b8a6", "#ec4899", "#64748b", "#94a3b8"];

function pctDelta(cur: number, prev: number): number | null {
  return prev ? ((cur - prev) / Math.abs(prev)) * 100 : null;
}

function Delta({ v }: { v: number | null }) {
  if (v === null) return null;
  const up = v >= 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>
      {up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
      {(up ? "+" : "") + v.toFixed(1)}%
    </span>
  );
}

function Kpi({ icon, label, value, delta, sub, accent }: {
  icon: React.ReactNode; label: string; value: string; delta?: number | null; sub?: string; accent?: boolean;
}) {
  return (
    <Card>
      <CardBody className="p-4">
        <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <span className={"shrink-0 " + (accent ? "text-indigo-600 dark:text-indigo-400" : "")}>{icon}</span>
          <span className="line-clamp-2 min-w-0 text-xs font-medium leading-tight" title={label}>{label}</span>
        </div>
        <div className={`mt-2 truncate text-xl font-bold tabular-nums tracking-tight md:text-2xl ${accent ? "text-indigo-600 dark:text-indigo-400" : "text-slate-900 dark:text-slate-50"}`} title={value}>
          <FitText>{value}</FitText>
        </div>
        <div className="mt-1 flex items-center gap-2">
          {delta !== undefined && <Delta v={delta ?? null} />}
          {sub && <span className="truncate text-xs text-slate-400 dark:text-slate-400">{sub}</span>}
        </div>
      </CardBody>
    </Card>
  );
}

// 내/외국인 적층 가로 막대 (pickKey 주면 막대 클릭 → 필터, active = 강조)
function SegBar({ title, sub, rows, height, C, pickKey, onPick, active, right, showShare }: {
  title: string; sub: string; rows: any[]; height: number; C: any;
  pickKey?: keyof Filters; onPick?: (k: keyof Filters, v: string) => void; active?: string[]; right?: React.ReactNode; showShare?: boolean;
}) {
  const data = rows
    .map((r) => {
      const gmv = r.gmv || 0;
      // 전체 매출 비중 = 해당 항목 GMV ÷ 전체 합계(grand_total, 백엔드 윈도우 합 · 표시 항목 수와 무관한 진짜 총합)
      const share = showShare && r.grand_total ? (gmv / r.grand_total) * 100 : null;
      return { name: r.name, dom: Math.max(0, gmv - (r.foreign_gmv || 0)), fgn: Math.max(0, r.foreign_gmv || 0), gmv,
               label: share != null ? `${compact(gmv)} (${share.toFixed(1)}%)` : compact(gmv) };
    })
    .sort((a, b) => b.gmv - a.gmv);
  const clickable = !!(pickKey && onPick);
  const hasActive = !!(active && active.length);
  const op = (name: string) => (hasActive ? (active!.includes(name) ? 1 : 0.28) : 1);
  const pick = clickable ? (d: any) => { const nm = d?.name ?? d?.payload?.name; if (nm != null) onPick!(pickKey!, String(nm)); } : undefined;
  // 막대 끝 라벨: 단일 <text>로 직접 렌더 → recharts Text의 공백 자동 줄바꿈("1504만 (2.7%)"가 2행으로 갈리던 문제) 방지.
  const barEndLabel = (p: any) => {
    if (p?.value == null) return null;
    return (
      <text x={(p.x || 0) + (p.width || 0) + 6} y={(p.y || 0) + (p.height || 0) / 2} dy={4}
            fontSize={11} fill={C.ttFg} textAnchor="start">{p.value}</text>
    );
  };
  return (
    <Card>
      <CardBody>
        <SectionTitle title={title} sub={clickable ? `${sub} · 막대 클릭=필터` : sub} right={right} />
        {data.length === 0 ? (
          <div className="flex items-center justify-center text-sm text-slate-400" style={{ height }}>데이터 없음</div>
        ) : (
          <ResponsiveContainer width="100%" height={height}>
            <BarChart data={data} layout="vertical" margin={{ left: 8, right: showShare ? 96 : 52, top: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.grid} horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} />
              <YAxis type="category" dataKey="name" width={140} interval={0} tick={<CatTick fill={C.ttFg} width={130} />} tickLine={false} axisLine={false} />
              <Tooltip
                formatter={(v: any, n: any, item: any) => {
                  const tot = item?.payload?.gmv || 0;
                  return [`${won(v as number)} (${tot ? ((v / tot) * 100).toFixed(1) : 0}%)`, n];
                }}
                contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }}
                cursor={{ fill: C.cursor }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="dom" name="내국인" stackId="a" fill={C.dom} onClick={pick} cursor={clickable ? "pointer" : undefined} isAnimationActive={false}>
                {clickable && data.map((d, i) => <Cell key={i} fill={C.dom} fillOpacity={op(d.name)} />)}
              </Bar>
              <Bar dataKey="fgn" name="외국인" stackId="a" fill={C.fgn} radius={[0, 4, 4, 0]} onClick={pick} cursor={clickable ? "pointer" : undefined} isAnimationActive={false}>
                {clickable && data.map((d, i) => <Cell key={i} fill={C.fgn} fillOpacity={op(d.name)} />)}
                <LabelList dataKey="label" content={barEndLabel} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardBody>
    </Card>
  );
}

function Donut({ title, sub, rows, C, pickKey, onPick, active, right }: {
  title: string; sub: string; rows: { name: string; value: number }[]; C: any;
  pickKey?: keyof Filters; onPick?: (k: keyof Filters, v: string) => void; active?: string[]; right?: React.ReactNode;
}) {
  const tot = rows.reduce((s, r) => s + r.value, 0);
  const clickable = !!(pickKey && onPick);
  const hasActive = !!(active && active.length);
  const pick = clickable ? (d: any) => { const nm = d?.name ?? d?.payload?.name; if (nm != null) onPick!(pickKey!, String(nm)); } : undefined;
  return (
    <Card>
      <CardBody>
        <SectionTitle title={title} sub={clickable ? `${sub} · 조각 클릭=필터` : sub} right={right} />
        {rows.length === 0 ? (
          <div className="flex h-[300px] items-center justify-center text-sm text-slate-400">데이터 없음</div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <PieChart>
              <Pie data={rows} dataKey="value" nameKey="name" innerRadius={62} outerRadius={104} paddingAngle={1.5} label={pieLabel(C)} labelLine={false} onClick={pick} cursor={clickable ? "pointer" : undefined} isAnimationActive={false}>
                {rows.map((r, i) => <Cell key={i} fill={PIE[i % PIE.length]} stroke={C.ttBg} strokeWidth={2} fillOpacity={hasActive ? (active!.includes(r.name) ? 1 : 0.3) : 1} />)}
              </Pie>
              <Tooltip
                formatter={(v: any, n: any) => [`${won(v as number)} (${tot ? (((v as number) / tot) * 100).toFixed(1) : 0}%)`, n]}
                contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }}
                itemStyle={{ color: C.ttFg }}
                labelStyle={{ color: C.ttFg }}
              />
              <Legend wrapperStyle={{ fontSize: 11 }} formatter={(value: any) => <span style={{ color: C.ttFg }}>{value}</span>} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardBody>
    </Card>
  );
}

// 섹션 우측 상단 요약 — 전체 내/외국인 GMV+비중 · 매입/위탁 GMV+비중 (현재 필터 기준)
function SectionStat({ cur, byBiz }: { cur: Summary | null; byBiz: any[] }) {
  if (!cur || !cur.gmv) return null;
  const tot = cur.gmv;
  const fgn = Math.max(0, cur.foreign_gmv || 0);
  const dom = Math.max(0, tot - fgn);
  const mi = byBiz.find((b) => b.name === "매입")?.gmv || 0;
  const wt = byBiz.find((b) => b.name === "위탁")?.gmv || 0;
  const pct = (v: number) => (tot ? ((v / tot) * 100).toFixed(0) : "0");
  const Item = ({ k, v, c }: { k: string; v: number; c: string }) => (
    <span className="whitespace-nowrap"><span className={`font-medium ${c}`}>{k}</span> {compact(v)} <span className="text-slate-400 dark:text-slate-400">({pct(v)}%)</span></span>
  );
  return (
    <div className="hidden flex-col items-end gap-0.5 text-[11px] leading-snug text-slate-500 sm:flex dark:text-slate-400">
      <div className="flex gap-2.5"><Item k="내국인" v={dom} c="text-slate-600 dark:text-slate-300" /><Item k="외국인" v={fgn} c="text-sky-600 dark:text-sky-400" /></div>
      <div className="flex gap-2.5"><Item k="매입" v={mi} c="text-slate-600 dark:text-slate-300" /><Item k="위탁" v={wt} c="text-violet-600 dark:text-violet-400" /></div>
    </div>
  );
}

function brandCsv(rows: any[], storeCols: string[] = []) {
  const head = ["사업구분", "브랜드", "상품수", "순판매수량", "GMV", "정상가매출", "실결제", "순이익(NetTake)", "외국인GMV", "외국인비중%", "할인율%", ...storeCols];
  const esc = (v: any) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const won0 = (v: any) => (v == null ? "" : Math.round(v));
  const lines = [head.join(",")];
  for (const r of rows) lines.push([r.business_type, r.brand_nm, r.goods, Math.round(r.qty), Math.round(r.gmv), Math.round(r.normal_amt), Math.round(r.pay), won0(r.net_take), Math.round(r.foreign_gmv), r.foreign_ratio.toFixed(1), r.discount_rate.toFixed(1), ...storeCols.map((s) => Math.round(r[s] || 0))].map(esc).join(","));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "offline_sales_by_brand.csv"; a.click(); URL.revokeObjectURL(a.href);
}

async function goodsCsv(qs: string) {
  const r = await fetch("/api/sales/goods.csv" + qs, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!r.ok) { alert("CSV 실패 (" + r.status + ")"); return; }
  const blob = await r.blob();
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "offline_sales_by_goods.csv"; a.click(); URL.revokeObjectURL(a.href);
}

export default function Dashboard({ meta, dark, filters, onPick }: { meta: Meta; dark: boolean; filters: Filters; onPick?: (k: keyof Filters, v: string) => void }) {
  void meta;
  const C = dark
    ? { grid: "#1e293b", axis: "#94a3b8", ttFg: "#cbd5e1", ttBg: "#1e293b", ttBorder: "#475569", bar: "#818cf8", cursor: "rgba(129,140,248,0.14)", dom: "#818cf8", fgn: "#94b8e8", wt: "#818cf8", mi: "#a78bfa", etc: "#94a3b8" }
    : { grid: "#f1f5f9", axis: "#94a3b8", ttFg: "#475569", ttBg: "#ffffff", ttBorder: "#e2e8f0", bar: "#4f46e5", cursor: "#f8fafc", dom: "#4f46e5", fgn: "#94b8e8", wt: "#4f46e5", mi: "#7c3aed", etc: "#94a3b8" };

  const f = filters;
  const [cur, setCur] = useState<Summary | null>(null);
  const [prev, setPrev] = useState<Summary | null>(null);
  const [aov, setAov] = useState<any>(null);
  const [aovPrev, setAovPrev] = useState<any>(null);
  const [hourly, setHourly] = useState<any[]>([]);
  const [cmpTrend, setCmpTrend] = useState<any[]>([]); // GMV 추이 직전 동기간(항상 표시)
  const [trend, setTrend] = useState<any[]>([]);
  const [byStore, setByStore] = useState<any[]>([]);
  const [byBiz, setByBiz] = useState<any[]>([]);
  const [byBrand, setByBrand] = useState<any[]>([]);
  const [catTop, setCatTop] = useState<any[]>([]);
  const [catMed, setCatMed] = useState<any[]>([]);
  const [brands, setBrands] = useState<any[]>([]);
  const [brandSC, setBrandSC] = useState<string[]>([]);
  const [goods, setGoods] = useState<any[]>([]);
  const [footfall, setFootfall] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  // 좁은 화면(모바일): 상품별 상세 표를 핵심 열 우선 + 좌측고정 해제로 재구성
  const [isNarrow, setIsNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const h = (e: MediaQueryListEvent) => setIsNarrow(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);

  const qs = useMemo(() => toQuery(f), [f]);
  const prevQs = useMemo(() => {
    const pr = prevRange(f.date_from, f.date_to);
    return toQuery({ ...f, date_from: pr.from, date_to: pr.to });
  }, [f]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    const tq = qs + (qs ? "&" : "?") + "gran=" + f.gran + "&split=business";
    // #1 크로스필터: 각 차원 차트는 '자기 차원'을 필터에서 빼고 조회 → 전체 표시(선택만 강조/나머지 흐림).
    // KPI·추이·상세표·시간대·요약은 전체 필터(qs) 그대로 → 선택 값으로 필터됨.
    const qNoStore = toQuery({ ...f, store: [] });
    const qNoBrand = toQuery({ ...f, brand: [] });
    const qNoCat = toQuery({ ...f, cat_top: [] });
    Promise.allSettled([
      api.summary(qs), api.summary(prevQs), api.trend(tq),
      api.by("store", qNoStore, 100), api.by("business", qs), api.by("brand", qNoBrand, 30),
      api.by("cat_top", qNoCat, 100), api.by("cat_medium", qs, 100),
      api.salesBrands(qs), api.salesGoods(qs, 1500),
      api.aov(qs), api.aov(prevQs),
      api.hourly(qs),
      api.footfall(qs),
    ]).then((res) => {
      if (!alive) return;
      const val = (i: number) => (res[i].status === "fulfilled" ? (res[i] as any).value : undefined);
      setCur(val(0) ?? null); setPrev(val(1) ?? null); setTrend(val(2) ?? []);
      setByStore(val(3) ?? []); setByBiz(val(4) ?? []); setByBrand(val(5) ?? []);
      setCatTop(val(6) ?? []); setCatMed(val(7) ?? []);
      const bd = val(8) ?? {}; setBrands(bd.rows ?? []); setBrandSC(bd.store_cols ?? []);
      const gd2 = val(9) ?? {}; setGoods(gd2.rows ?? []);
      setAov(val(10) ?? null); setAovPrev(val(11) ?? null);
      setHourly(val(12) ?? []);
      setFootfall(val(13) ?? null);
      const failed = res.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
      if (failed) setError(failed.reason?.message || "일부 데이터를 불러오지 못했습니다");
    }).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, prevQs, f.gran, reloadKey]);

  // 위탁/매입 적층 추이
  const trendBiz = useMemo(() => {
    const m = new Map<string, any>();
    for (const r of trend) {
      const row = m.get(r.bucket) || { bucket: r.bucket, 위탁: 0, 매입: 0, 기타: 0 };
      row[r.business_type] = (row[r.business_type] || 0) + (r.gmv || 0);
      m.set(r.bucket, row);
    }
    return [...m.values()];
  }, [trend]);
  // 직전 동기간 비교: 선택 기간과 같은 길이의 바로 직전 구간 추이(총 GMV)를 조회해 버킷 순서(index)로 매칭 → 선 오버레이
  useEffect(() => {
    if (!f.date_from || !f.date_to) { setCmpTrend([]); return; }
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const d0 = new Date(f.date_from + "T00:00:00"), d1 = new Date(f.date_to + "T00:00:00");
    const lenDays = Math.round((d1.getTime() - d0.getTime()) / 86400000) + 1; // 포함 일수
    const cFrom = new Date(d0); cFrom.setDate(d0.getDate() - lenDays);       // 직전 구간 시작
    const cTo = new Date(d1); cTo.setDate(d1.getDate() - lenDays);           // 직전 구간 끝(= 선택 시작 −1일)
    const cq = toQuery({ ...f, date_from: iso(cFrom), date_to: iso(cTo) });
    const ctq = cq + (cq ? "&" : "?") + "gran=" + f.gran;
    let alive = true;
    api.trend(ctq).then((r: any) => { if (alive) setCmpTrend(r || []); }).catch(() => { if (alive) setCmpTrend([]); });
    return () => { alive = false; };
  }, [qs, f.gran]); // eslint-disable-line react-hooks/exhaustive-deps
  const trendData = useMemo(
    () => trendBiz.map((row: any, i: number) => ({ ...row, cmp: cmpTrend[i]?.gmv ?? null })),
    [trendBiz, cmpTrend]
  );

  const catMedTop = useMemo(() => {
    const sorted = [...catMed].filter((c) => c.gmv > 0).sort((a, b) => b.gmv - a.gmv);
    const top = sorted.slice(0, 10).map((c) => ({ name: c.name, value: c.gmv }));
    const etc = sorted.slice(10).reduce((s, c) => s + c.gmv, 0);
    if (etc > 0) top.push({ name: "기타", value: etc });
    return top;
  }, [catMed]);
  const catTopPie = useMemo(() => catTop.filter((c) => c.gmv > 0).map((c) => ({ name: c.name, value: c.gmv })), [catTop]);
  const hourlyData = useMemo(
    () => hourly.map((h: any) => ({ hour: h.hour, dom: Math.max(0, (h.gmv || 0) - (h.foreign_gmv || 0)), fgn: Math.max(0, h.foreign_gmv || 0), gmv: h.gmv || 0, receipts: h.receipts || 0 })),
    [hourly]
  );

  const granLabel = f.gran === "day" ? "일" : f.gran === "week" ? "주" : "월";

  // 점별(매장) 재고 컬럼을 동적으로 덧붙임 (#2)
  // #3 GMV 비중 열 + 값에 따른 스펙트럼(히트맵) 배경색
  const brandTot = useMemo(() => brands.reduce((s: number, r: any) => s + (r.gmv || 0), 0), [brands]);
  const brandRows = useMemo(() => brands.map((r: any) => ({ ...r, gmv_ratio: brandTot ? (r.gmv / brandTot) * 100 : 0, net_take_rate: (r.net_take != null && r.gmv) ? (r.net_take / r.gmv) * 100 : null })), [brands, brandTot]);
  const brandMaxRatio = useMemo(() => brandRows.reduce((mx: number, r: any) => Math.max(mx, r.gmv_ratio || 0), 0) || 1, [brandRows]);
  const gmvRatioCol = useMemo(
    () => colNum("gmv_ratio", "GMV비중", "num", {
      minWidth: 92,
      valueFormatter: (p: any) => (p.value ?? 0).toFixed(1) + "%",
      cellStyle: (p: any) => {
        const a = Math.max(0, Math.min(1, (p.value || 0) / brandMaxRatio));
        const [base, scale] = dark ? [0.14, 0.46] : [0.06, 0.5];
        const rgb = dark ? "129,140,248" : "99,102,241";
        return { textAlign: "right", backgroundColor: `rgba(${rgb},${(base + scale * a).toFixed(3)})`, ...(dark ? { color: "#e2e8f0" } : {}), fontWeight: a > 0.55 ? 600 : 400 };
      },
    }),
    [brandMaxRatio, dark]
  );
  const brandNtCol = useMemo(() => ntRateCol(brandRows, dark), [brandRows, dark]);
  const brandCols = useMemo(() => {
    const base = [...BRAND_COLS];
    const gi = base.findIndex((c: any) => c.field === "gmv");
    if (gi >= 0) base.splice(gi + 1, 0, gmvRatioCol);
    const ni = base.findIndex((c: any) => c.field === "net_take");
    if (ni >= 0) base.splice(ni + 1, 0, brandNtCol);
    return base;   // 점별 재고(피벗)는 화면에서 제외 — CSV에만 포함
  }, [gmvRatioCol, brandNtCol]);
  const brandTotal = useMemo(() => (brandRows.length ? [brandTotalRow(brandRows)] : []), [brandRows]);
  const goodsRows = useMemo(() => goods.map((r: any) => ({ ...r, net_take_rate: (r.net_take != null && r.gmv) ? (r.net_take / r.gmv) * 100 : null })), [goods]);
  const goodsNtCol = useMemo(() => ntRateCol(goodsRows, dark), [goodsRows, dark]);
  const goodsCols = useMemo(() => {
    // 순이익 뒤에 순이익률(색상) 열 삽입. 점별 재고 피벗 열은 화면 제외(운영중매장수·점재고합계로 요약, 상세는 CSV).
    const withRate = (cols: any[]) => {
      const i = cols.findIndex((c: any) => c.field === "net_take");
      if (i < 0) return cols;
      const c = [...cols]; c.splice(i + 1, 0, goodsNtCol); return c;
    };
    if (!isNarrow) return withRate(GOODS_COLS);
    // 모바일: 좌측고정 해제 + 핵심 열(상품·GMV·순판매·할인율) 우선, 나머지는 뒤로 가로 스크롤
    const unpin = (c: any) => ({ ...c, pinned: null });
    const pick = (f: string) => unpin(GOODS_COLS.find((c: any) => c.field === f));
    const primaryF = ["goods_nm", "gmv", "qty", "discount_rate"];
    const primary = [{ ...pick("goods_nm"), minWidth: 150 }, pick("gmv"), pick("qty"), pick("discount_rate")];
    const rest = GOODS_COLS.filter((c: any) => !primaryF.includes(c.field)).map(unpin);
    return withRate([...primary, ...rest]);
  }, [isNarrow, goodsNtCol]);
  const goodsTotal = useMemo(() => (goodsRows.length ? [goodsTotalRow(goodsRows)] : []), [goodsRows]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400 dark:text-slate-400">
          기간 {f.date_from} ~ {f.date_to} · {granLabel} 단위 · 신장율 = 직전 동기간 대비 · 외국인 = 면세(tax refund) 기준
        </p>
        {loading && <Spinner className="h-4 w-4" />}
      </div>

      {error && (
        <div className="flex items-center justify-between rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400">
          <span>⚠ {error}</span>
          <button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 hover:bg-rose-200 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button>
        </div>
      )}

      {/* KPI */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi icon={<TrendingUp size={16} />} label="GMV (판매가×수량)" accent value={cur ? won(cur.gmv) : "—"} delta={cur && prev ? pctDelta(cur.gmv, prev.gmv) : null} />
        <Kpi icon={<Receipt size={16} />} label="객단가 (영수증당)" accent value={aov ? won(aov.aov) : "—"} delta={aov && aovPrev ? pctDelta(aov.aov, aovPrev.aov) : null} />
        <Kpi icon={<Tag size={16} />} label="정상가 매출" value={cur ? won(cur.normal_amt) : "—"} delta={cur && prev ? pctDelta(cur.normal_amt, prev.normal_amt) : null} />
        <Kpi icon={<ShoppingBag size={16} />} label="순판매수량" value={cur ? num(cur.qty) : "—"} delta={cur && prev ? pctDelta(cur.qty, prev.qty) : null} />
        <Kpi icon={<Globe size={16} />} label="외국인 매출(면세)" value={cur ? won(cur.foreign_gmv) : "—"} delta={cur && prev ? pctDelta(cur.foreign_gmv, prev.foreign_gmv) : null} />
        <Kpi icon={<Wallet size={16} />} label="실결제액" value={cur ? won(cur.pay) : "—"} delta={cur && prev ? pctDelta(cur.pay, prev.pay) : null} />
        {cur?.net_take != null && (
          <Kpi icon={<Wallet size={16} />} label="순이익 (Net Take)" value={won(cur.net_take)}
               delta={cur && prev && prev.net_take != null ? pctDelta(cur.net_take, prev.net_take) : null}
               sub="정산 profit · 오늘자 잠정 추정 포함" />
        )}
        <Kpi icon={<Percent size={16} />} label="평균 할인율" value={cur ? cur.discount_rate.toFixed(1) + "%" : "—"} />
        <Kpi icon={<Globe size={16} />} label="외국인 매출 비중" value={cur ? cur.foreign_ratio.toFixed(1) + "%" : "—"} />
        <Kpi icon={<Boxes size={16} />} label="거래 상품 수" value={cur ? num(cur.goods_count) : "—"} />
        <Kpi icon={<Footprints size={16} />} label="입객수 (매장 방문)" value={footfall?.available ? num(footfall.totals.visitors) : "—"} sub="온라인 트래픽 아님 · 매장/기간 기준" />
        <Kpi icon={<Percent size={16} />} label="구매전환율 (구매÷입객)" accent value={footfall?.available ? footfall.totals.conversion.toFixed(1) + "%" : "—"} sub="상품 필터 미반영(매장 단위)" />
      </div>
      <p className="-mt-2 text-[11px] text-slate-400 dark:text-slate-400">
        ※ 객단가 = 판매가 합 ÷ 영수증 수(주문 건수, 중복 제거). 기간·매장·매장타입·<b>사업구분·브랜드·카테(최상위/대/중)</b> 필터 반영(상품 UID·MD 제외){aov ? ` · 영수증 ${num(aov.receipts)}건` : ""}
      </p>

      {/* ── 매장 (최상단) ── */}
      <SegBar title="매장별 GMV" sub="내국인 / 외국인(면세)" rows={byStore} height={430} C={C} pickKey="store" onPick={onPick} active={f.store} right={<SectionStat cur={cur} byBiz={byBiz} />} />

      {/* ── 브랜드 ── */}
      <SegBar title="브랜드 GMV Top 30" sub="내국인 / 외국인(면세) · 금액(전체 매출 비중)" rows={byBrand} height={620} C={C} pickKey="brand" onPick={onPick} active={f.brand} showShare right={<SectionStat cur={cur} byBiz={byBiz} />} />
      <Card>
        <CardBody>
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">브랜드별 상세</h3>
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">판매 발생 전체 · 총 {num(brands.length)}개 · 열 고정/이동/정렬 가능</p>
            </div>
            <div className="flex items-center gap-3">
              <SectionStat cur={cur} byBiz={byBiz} />
              <button onClick={() => brandCsv(brands, brandSC)} disabled={!brands.length}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">
                <Download size={14} /> CSV
              </button>
            </div>
          </div>
          <DataGrid rows={brandRows} columns={brandCols} dark={dark} height={440} pinnedTop={brandTotal} />
        </CardBody>
      </Card>

      {/* GMV 추이 (위탁/매입 세로 막대) + 직전 동기간 비교선 */}
      <Card>
        <CardBody>
          <SectionTitle
            title="GMV 추이"
            sub={`위탁 / 매입 · ${granLabel} 단위 · 직전 동기간(선) 비교`}
            right={<SectionStat cur={cur} byBiz={byBiz} />}
          />
          {trendData.length === 0 ? (
            <div className="flex h-[300px] items-center justify-center text-sm text-slate-400">데이터 없음</div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={trendData} margin={{ left: 8, right: 8, top: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
                <XAxis dataKey="bucket" tick={<HolidayTick axisColor={C.axis} day={f.gran === "day"} format={(v: string) => v.slice(5)} />} tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} width={48} />
                <Tooltip
                  formatter={(v: any, n: any, item: any) => {
                    if (n === "직전 동기") return [won(v as number), "직전 동기간"];
                    const p = item?.payload || {};
                    const tot = (p["위탁"] || 0) + (p["매입"] || 0);
                    return [`${won(v as number)} (${tot ? ((Number(v) / tot) * 100).toFixed(1) : 0}%)`, n];
                  }}
                  labelFormatter={(label: any, payload: any) => {
                    const p = payload && payload[0] && payload[0].payload;
                    const tot = p ? (p["위탁"] || 0) + (p["매입"] || 0) : 0;
                    return `${label} · 총 ${won(tot)}`;
                  }}
                  labelStyle={{ color: C.ttFg }}
                  cursor={{ fill: C.cursor }}
                  contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="위탁" stackId="1" fill={C.wt} isAnimationActive={false} />
                <Bar dataKey="매입" stackId="1" fill={C.mi} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  <LabelList position="top" valueAccessor={(e: any) => { const p = e?.payload || {}; return (p["위탁"] || 0) + (p["매입"] || 0); }} content={trendLabel(trendData.length, compact, C.ttFg)} />
                </Bar>
                <Line
                  type="monotone"
                  dataKey="cmp"
                  name="직전 동기"
                  stroke={dark ? "#fbbf24" : "#ea580c"}
                  strokeWidth={3}
                  dot={{ r: 3, fill: dark ? "#fbbf24" : "#ea580c", stroke: C.ttBg, strokeWidth: 1.5 }}
                  activeDot={{ r: 5, fill: dark ? "#fbbf24" : "#ea580c", stroke: C.ttBg, strokeWidth: 2 }}
                  connectNulls
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </CardBody>
      </Card>

      {/* 시간대별 매출 (GMV 추이 아래) — 내/외국인 적층 */}
      <Card>
        <CardBody>
          <SectionTitle title="시간대별 매출" sub="완료주문 거래시각(KST) · 내국인/외국인 · 10~23시" right={<SectionStat cur={cur} byBiz={byBiz} />} />
          {hourlyData.length === 0 || hourlyData.every((h) => !h.gmv) ? (
            <div className="flex h-[300px] items-center justify-center text-sm text-slate-400">데이터 없음</div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={hourlyData} margin={{ left: 8, right: 8, top: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
                <XAxis dataKey="hour" tickFormatter={(h: any) => `${h}시`} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} interval={0} />
                <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} width={48} />
                <Tooltip
                  formatter={(v: any, n: any, item: any) => { const tot = item?.payload?.gmv || 0; return [`${won(v as number)} (${tot ? ((Number(v) / tot) * 100).toFixed(1) : 0}%)`, n]; }}
                  labelFormatter={(h: any) => `${h}시`}
                  contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }}
                  cursor={{ fill: C.cursor }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="dom" name="내국인" stackId="h" fill={C.dom} />
                <Bar dataKey="fgn" name="외국인" stackId="h" fill={C.fgn} radius={[4, 4, 0, 0]}>
                  <LabelList valueAccessor={(e: any) => e?.payload?.gmv} position="top" formatter={(v: any) => compact(v as number)} fontSize={10} fill={C.ttFg} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardBody>
      </Card>

      {/* ── 카테 ── */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Donut title="최상위 카테 비중" sub="GMV" rows={catTopPie} C={C} pickKey="cat_top" onPick={onPick} active={f.cat_top} right={<SectionStat cur={cur} byBiz={byBiz} />} />
        <Donut title="중카테 비중" sub="GMV · Top 10 + 기타" rows={catMedTop} C={C} right={<SectionStat cur={cur} byBiz={byBiz} />} />
      </div>

      {/* ── 상품 ── */}
      <Card>
        <CardBody>
          <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">상품별 상세</h3>
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">판매 발생 전체 · {num(goods.length)}개 · {isNarrow ? "핵심 열 우선 · 정렬·가로 스크롤" : "브랜드/상품 고정, 열 이동·정렬·가로 스크롤"}</p>
            </div>
            <div className="flex items-center gap-3">
              <SectionStat cur={cur} byBiz={byBiz} />
              <button onClick={() => goodsCsv(qs)} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700">
                <Download size={14} /> CSV (전체)
              </button>
            </div>
          </div>
          <DataGrid rows={goodsRows} columns={goodsCols} dark={dark} height={460} pinnedTop={goodsTotal} />
        </CardBody>
      </Card>
    </div>
  );
}
