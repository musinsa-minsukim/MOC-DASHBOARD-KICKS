import { useEffect, useMemo, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, PieChart, Pie, Cell, Legend, LabelList,
} from "recharts";
import { Globe, TrendingUp, TrendingDown, ShoppingBag, Percent, Receipt } from "lucide-react";
import { api, toQuery, won, num, compact, prevRange, type Filters } from "./lib";
import { HolidayTick } from "./holidays";
import { trendLabel, pieLabel, CatTick } from "./chartlabels";
import { Card, CardBody, SectionTitle, Spinner, FitText } from "./ui";

type Meta = { date_min: string; date_max: string; shop_types: string[]; business_types: string[] };
const FGN = "#94b8e8";
const SEX_COLORS: Record<string, string> = { 여성: "#ec4899", 남성: "#3b82f6", 기타: "#94a3b8" };
const MEMBER_PIE = ["#4f46e5", "#94a3b8", "#c7d2fe"];
const AGE_ORDER = ["초등학생", "중학생", "고등학생", "대학생", "20대초반", "20대후반", "30대초반", "30대후반", "40대초반", "40대후반", "50대초반", "50대후반", "60대이상", "기타"];

function pctDelta(c: number, p: number): number | null { return p ? ((c - p) / Math.abs(p)) * 100 : null; }
function Delta({ v }: { v: number | null }) {
  if (v === null) return null;
  const up = v >= 0;
  return <span className={`inline-flex items-center gap-0.5 text-xs font-semibold ${up ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{up ? <TrendingUp size={11} /> : <TrendingDown size={11} />}{(up ? "+" : "") + v.toFixed(1)}%</span>;
}
function Kpi({ icon, label, value, delta, accent }: { icon: React.ReactNode; label: string; value: string; delta?: number | null; accent?: boolean }) {
  return (
    <Card><CardBody className="p-4">
      <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400"><span className={"shrink-0 " + (accent ? "text-indigo-600 dark:text-indigo-400" : "")}>{icon}</span><span className="line-clamp-2 min-w-0 text-xs font-medium leading-tight">{label}</span></div>
      <div className={`mt-2 truncate text-xl font-bold tabular-nums tracking-tight md:text-2xl ${accent ? "text-indigo-600 dark:text-indigo-400" : "text-slate-900 dark:text-slate-50"}`}><FitText>{value}</FitText></div>
      {delta !== undefined && <div className="mt-1"><Delta v={delta ?? null} /></div>}
    </CardBody></Card>
  );
}
function HBar({ title, sub, rows, valueKey, pctMode, C, pickKey, onPick, active }: {
  title: string; sub: string; rows: any[]; valueKey: string; pctMode?: boolean; C: any;
  pickKey?: keyof Filters; onPick?: (k: keyof Filters, v: string) => void; active?: string[];
}) {
  const data = [...rows].filter((r) => (r[valueKey] || 0) > 0).sort((a, b) => b[valueKey] - a[valueKey]);
  const total = data.reduce((s, r) => s + (r[valueKey] || 0), 0);
  const clickable = !!(pickKey && onPick);
  const hasActive = !!(active && active.length);
  const op = (name: string) => (hasActive ? (active!.includes(name) ? 1 : 0.28) : 1);
  const pick = clickable ? (dd: any) => { const nm = dd?.name ?? dd?.payload?.name; if (nm != null) onPick!(pickKey!, String(nm)); } : undefined;
  return (
    <Card><CardBody>
      <SectionTitle title={title} sub={clickable ? `${sub} · 막대 클릭=필터` : sub} />
      {data.length === 0 ? <div className="flex h-[400px] items-center justify-center text-sm text-slate-400">데이터 없음</div> : (
        <ResponsiveContainer width="100%" height={430}>
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 44, top: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.grid} horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={(v) => (pctMode ? v + "%" : compact(v))} />
            <YAxis type="category" dataKey="name" width={140} interval={0} tick={<CatTick fill={C.ttFg} width={130} />} tickLine={false} axisLine={false} />
            <Tooltip formatter={(v: any) => [pctMode ? (v as number).toFixed(1) + "%" : `${won(v as number)} (${total ? (((v as number) / total) * 100).toFixed(1) : 0}%)`, pctMode ? "외국인 비중" : "외국인 매출"]} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} cursor={{ fill: C.cursor }} />
            <Bar dataKey={valueKey} fill={FGN} radius={[0, 4, 4, 0]} onClick={pick} cursor={clickable ? "pointer" : undefined} isAnimationActive={false}>
              {clickable && data.map((dd, i) => <Cell key={i} fill={FGN} fillOpacity={op(dd.name)} />)}
              <LabelList dataKey={valueKey} position="right" formatter={(v: any) => (pctMode ? (v as number).toFixed(1) + "%" : compact(v as number))} fontSize={11} fill={C.ttFg} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </CardBody></Card>
  );
}

export default function Customer({ meta, dark, filters, onPick }: { meta: Meta; dark: boolean; filters: Filters; onPick?: (k: keyof Filters, v: string) => void }) {
  void meta;
  const C = dark
    ? { grid: "#1e293b", axis: "#94a3b8", ttFg: "#cbd5e1", ttBg: "#1e293b", ttBorder: "#475569", cursor: "rgba(129,140,248,0.14)", bar: "#818cf8" }
    : { grid: "#f1f5f9", axis: "#94a3b8", ttFg: "#475569", ttBg: "#ffffff", ttBorder: "#e2e8f0", cursor: "#f8fafc", bar: "#4f46e5" };
  const f = filters;
  const [cur, setCur] = useState<any>(null);
  const [prev, setPrev] = useState<any>(null);
  const [trend, setTrend] = useState<any[]>([]);
  const [byStore, setByStore] = useState<any[]>([]);
  const [demo, setDemo] = useState<any>(null);
  const [aov, setAov] = useState<any>(null);
  const [aovPrev, setAovPrev] = useState<any>(null);
  const [footfall, setFootfall] = useState<any>(null);
  const [country, setCountry] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const qs = useMemo(() => toQuery(f), [f]);
  const prevQs = useMemo(() => { const p = prevRange(f.date_from, f.date_to); return toQuery({ ...f, date_from: p.from, date_to: p.to }); }, [f]);
  const custQs = useMemo(() => {
    const p = new URLSearchParams();
    if (f.date_from) p.append("date_from", f.date_from);
    if (f.date_to) p.append("date_to", f.date_to);
    f.type.forEach((t) => p.append("type", t));
    f.store.forEach((s) => p.append("store", s));
    return p.toString() ? "?" + p.toString() : "";
  }, [f]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    Promise.allSettled([
      api.summary(qs), api.summary(prevQs),
      api.trend(qs + (qs ? "&" : "?") + "gran=" + f.gran),
      api.by("store", qs, 100), api.customer(custQs),
      api.aov(qs), api.aov(prevQs),
      api.footfall(custQs), api.customerCountry(custQs, 15),
    ]).then((res) => {
      if (!alive) return;
      const val = (i: number) => (res[i].status === "fulfilled" ? (res[i] as any).value : undefined);
      setCur(val(0) ?? null); setPrev(val(1) ?? null); setTrend(val(2) ?? []); setByStore(val(3) ?? []); setDemo(val(4) ?? null);
      setAov(val(5) ?? null); setAovPrev(val(6) ?? null);
      setFootfall(val(7) ?? null); setCountry(val(8) ?? null);
      const failed = res.find((r) => r.status === "rejected") as PromiseRejectedResult | undefined;
      if (failed) setError(failed.reason?.message || "일부 데이터를 불러오지 못했습니다");
    }).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, prevQs, custQs, f.gran, reloadKey]);

  const storeRatio = useMemo(() => byStore.map((s) => ({ name: s.name, ratio: s.gmv ? (s.foreign_gmv / s.gmv) * 100 : 0 })), [byStore]);
  const sexPie = useMemo(() => (demo?.sex ?? []).filter((r: any) => r.name !== "기타").map((r: any) => ({ name: r.name, value: r.gmv })), [demo]);
  const memberPie = useMemo(() => (demo?.member ?? []).map((r: any) => ({ name: r.name, value: r.gmv })), [demo]);
  const ageBars = useMemo(() => {
    const rows = (demo?.age ?? []).filter((r: any) => r.name !== "기타");
    const tot = rows.reduce((s: number, r: any) => s + r.gmv, 0);
    return rows.map((r: any) => ({ name: r.name, 비중: tot ? (r.gmv / tot) * 100 : 0, gmv: r.gmv }))
      .sort((a: any, b: any) => AGE_ORDER.indexOf(a.name) - AGE_ORDER.indexOf(b.name));
  }, [demo]);

  const fgmv = cur?.foreign_gmv ?? 0;
  const granLabel = f.gran === "day" ? "일" : f.gran === "week" ? "주" : "월";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400 dark:text-slate-400">외국인 = 면세(tax refund) · 판매 탭(MOSS)과 동일 기준, 모든 필터 반영 · 성별/연령/회원 분포는 고객요약(기간·매장·매장타입만 반영)</p>
        {loading && <Spinner className="h-4 w-4" />}
      </div>

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi icon={<TrendingUp size={16} />} label="총 GMV" value={cur ? won(cur.gmv) : "—"} delta={cur && prev ? pctDelta(cur.gmv, prev.gmv) : null} />
        <Kpi icon={<Globe size={16} />} label="외국인 매출(면세)" accent value={cur ? won(fgmv) : "—"} delta={cur && prev ? pctDelta(fgmv, prev.foreign_gmv) : null} />
        <Kpi icon={<Percent size={16} />} label="외국인 매출 비중" value={cur ? (cur.foreign_ratio).toFixed(1) + "%" : "—"} />
        <Kpi icon={<ShoppingBag size={16} />} label="순판매수량" value={cur ? num(cur.qty) : "—"} delta={cur && prev ? pctDelta(cur.qty, prev.qty) : null} />
        <Kpi icon={<Receipt size={16} />} label="내국인 객단가" value={aov ? won(aov.domestic.aov) : "—"} delta={aov && aovPrev ? pctDelta(aov.domestic.aov, aovPrev.domestic.aov) : null} />
        <Kpi icon={<Receipt size={16} />} label="외국인 객단가" accent value={aov ? won(aov.foreign.aov) : "—"} delta={aov && aovPrev ? pctDelta(aov.foreign.aov, aovPrev.foreign.aov) : null} />
      </div>
      <p className="-mt-2 text-[11px] text-slate-400 dark:text-slate-400">
        ※ 객단가 = 판매가 합 ÷ 영수증 수(주문 건수, 중복 제거). 기간·매장·매장타입·사업구분·브랜드·카테·내외국인 반영(상품 UID·MD 제외){aov ? ` · 내국인 ${num(aov.domestic.receipts)}건 / 외국인 ${num(aov.foreign.receipts)}건` : ""}
      </p>

      <Card><CardBody>
        <SectionTitle title="외국인(면세) 매출 추이" sub={`${granLabel} 단위`} />
        {trend.length === 0 ? <div className="flex h-[280px] items-center justify-center text-sm text-slate-400">데이터 없음</div> : (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={trend} margin={{ left: 8, right: 8, top: 4 }}>
              <defs><linearGradient id="fg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={FGN} stopOpacity={0.4} /><stop offset="100%" stopColor={FGN} stopOpacity={0} /></linearGradient></defs>
              <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
              <XAxis dataKey="bucket" tick={<HolidayTick axisColor={C.axis} day={f.gran === "day"} format={(v: string) => String(v).slice(5)} />} tickLine={false} axisLine={false} minTickGap={24} />
              <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} width={48} />
              <Tooltip formatter={(v: any) => [won(v as number), "외국인 매출"]} labelStyle={{ color: C.ttFg }} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} />
              <Area type="monotone" dataKey="foreign_gmv" stroke={FGN} strokeWidth={2} fill="url(#fg)">
                <LabelList position="top" content={trendLabel(trend.length, compact, C.ttFg)} />
              </Area>
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardBody></Card>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <HBar title="매장별 외국인 매출" sub="면세 GMV" rows={byStore} valueKey="foreign_gmv" C={C} pickKey="store" onPick={onPick} active={f.store} />
        <HBar title="매장별 외국인 비중" sub="외국인 / 전체 GMV" rows={storeRatio} valueKey="ratio" pctMode C={C} pickKey="store" onPick={onPick} active={f.store} />
      </div>

      <h3 className="pt-1 text-sm font-semibold text-slate-500 dark:text-slate-400">글로벌 고객 · 매장 입객 (면세 국적 기준 gross · 온라인 트래픽과 무관)</h3>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card><CardBody>
          <SectionTitle title="국가별 GMV (글로벌 고객)" sub={country?.available ? `면세 국적 기준 · 상위 ${country.rows.length}개국 / 총 ${country.countries}개국` : "면세 국적 기준"} />
          {!country?.available || (country.rows ?? []).length === 0 ? (
            <div className="flex h-[430px] items-center justify-center text-sm text-slate-400">{country && !country.available ? "국가 데이터 준비 중 (다음 갱신 후 표시)" : "데이터 없음"}</div>
          ) : (
            <ResponsiveContainer width="100%" height={430}>
              <BarChart data={country.rows} layout="vertical" margin={{ left: 8, right: 56, top: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.grid} horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} />
                <YAxis type="category" dataKey="nationality" width={52} interval={0} tick={{ fontSize: 11, fill: C.ttFg }} tickLine={false} axisLine={false} />
                <Tooltip formatter={(v: any, _n: any, item: any) => [`${won(v as number)} (${(item?.payload?.share ?? 0).toFixed(1)}% · ${num(item?.payload?.buyers || 0)}건)`, "GMV"]} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} cursor={{ fill: C.cursor }} />
                <Bar dataKey="gmv" fill={FGN} radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  <LabelList dataKey="share" position="right" formatter={(v: any) => (v as number).toFixed(1) + "%"} fontSize={11} fill={C.ttFg} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardBody></Card>

        <Card><CardBody>
          <SectionTitle title="매장별 입객수 · 구매전환율" sub={footfall?.available ? `전환율 = 구매건수 ÷ 입객수${footfall.totals ? ` · 전체 ${footfall.totals.conversion.toFixed(1)}%` : ""}` : "입객수(footfall)"} />
          {!footfall?.available || (footfall.rows ?? []).length === 0 ? (
            <div className="flex h-[430px] items-center justify-center text-sm text-slate-400">{footfall && !footfall.available ? "입객 데이터 준비 중 (다음 갱신 후 표시)" : "데이터 없음"}</div>
          ) : (
            <div className="max-h-[430px] overflow-auto">
              <table className="w-full text-sm tabular-nums">
                <thead className="sticky top-0 bg-white text-xs font-medium text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                  <tr className="border-b border-slate-100 dark:border-slate-800">
                    <th className="py-2 pr-2 text-left font-medium">매장</th>
                    <th className="py-2 px-2 text-right font-medium">입객수</th>
                    <th className="py-2 px-2 text-right font-medium">구매건수</th>
                    <th className="py-2 px-2 text-right font-medium">전환율</th>
                    <th className="py-2 pl-2 text-right font-medium">객단가</th>
                  </tr>
                </thead>
                <tbody>
                  {footfall.rows.map((r: any, i: number) => (
                    <tr key={i} className="border-b border-slate-50 dark:border-slate-800/60">
                      <td className="max-w-[150px] truncate py-1.5 pr-2 text-slate-700 dark:text-slate-200">{r.store_name}</td>
                      <td className="px-2 py-1.5 text-right text-slate-600 dark:text-slate-300">{num(r.visitors)}</td>
                      <td className="px-2 py-1.5 text-right text-slate-600 dark:text-slate-300">{num(r.receipts)}</td>
                      <td className="px-2 py-1.5 text-right font-semibold text-indigo-600 dark:text-indigo-400">{r.conversion.toFixed(1)}%</td>
                      <td className="py-1.5 pl-2 text-right text-slate-600 dark:text-slate-300">{won(r.aov)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody></Card>
      </div>

      <h3 className="pt-1 text-sm font-semibold text-slate-500 dark:text-slate-400">고객 인구통계 — 고객요약 기준 (기간·매장타입만 반영)</h3>
      {!demo || (sexPie.length === 0 && memberPie.length === 0) ? (
        <Card><CardBody><div className="py-8 text-center text-sm text-slate-400">고객요약 데이터가 없습니다.</div></CardBody></Card>
      ) : (
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <Card><CardBody>
            <SectionTitle title="성별 비중" sub="기타 제외" />
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={sexPie} dataKey="value" nameKey="name" innerRadius={60} outerRadius={100} paddingAngle={1.5} label={pieLabel(C)} labelLine={false}>{sexPie.map((r: any, i: number) => <Cell key={i} fill={SEX_COLORS[r.name] || "#94a3b8"} stroke={C.ttBg} strokeWidth={2} />)}</Pie>
                <Tooltip formatter={(v: any, n: any) => [won(v as number), n]} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} itemStyle={{ color: C.ttFg }} labelStyle={{ color: C.ttFg }} />
                <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value: any) => <span style={{ color: C.ttFg }}>{value}</span>} />
              </PieChart>
            </ResponsiveContainer>
          </CardBody></Card>
          <Card><CardBody>
            <SectionTitle title="연령대 비중" sub="기타 제외" />
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={ageBars} margin={{ left: 0, right: 8, top: 16 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 10, fill: C.axis }} tickLine={false} axisLine={false} interval={0} angle={-30} textAnchor="end" height={50} />
                <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={(v) => v + "%"} width={36} />
                <Tooltip formatter={(v: any) => [(v as number).toFixed(1) + "%", "비중"]} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} cursor={{ fill: C.cursor }} />
                <Bar dataKey="비중" fill={C.bar} radius={[4, 4, 0, 0]}>
                  <LabelList dataKey="비중" position="top" formatter={(v: any) => (v as number).toFixed(1) + "%"} fontSize={10} fill={C.ttFg} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardBody></Card>
          <Card><CardBody>
            <SectionTitle title="회원 / 비회원" sub="GMV" />
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie data={memberPie} dataKey="value" nameKey="name" innerRadius={60} outerRadius={100} paddingAngle={1.5} label={pieLabel(C)} labelLine={false}>{memberPie.map((_: any, i: number) => <Cell key={i} fill={MEMBER_PIE[i % MEMBER_PIE.length]} stroke={C.ttBg} strokeWidth={2} />)}</Pie>
                <Tooltip formatter={(v: any, n: any) => [won(v as number), n]} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} itemStyle={{ color: C.ttFg }} labelStyle={{ color: C.ttFg }} />
                <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value: any) => <span style={{ color: C.ttFg }}>{value}</span>} />
              </PieChart>
            </ResponsiveContainer>
          </CardBody></Card>
        </div>
      )}
    </div>
  );
}
