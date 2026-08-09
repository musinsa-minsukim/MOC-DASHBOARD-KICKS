import { useEffect, useMemo, useState } from "react";
import { ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import { api, won, num, compact } from "./lib";
import { Card, CardBody, SectionTitle, Chip, Spinner, MultiSelect } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";

type Meta = { shop_types: string[]; stores: { store_name: string; shop_type: string }[] };

export default function Target({ meta, dark }: { meta: Meta; dark: boolean }) {
  const [d, setD] = useState<any>(null);
  const [month, setMonth] = useState<string>("");
  const [types, setTypes] = useState<string[]>([]);
  const [stores, setStores] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const C = dark
    ? { grid: "#1e293b", axis: "#94a3b8", ttFg: "#cbd5e1", ttBg: "#1e293b", ttBorder: "#475569", cursor: "rgba(129,140,248,0.14)", actual: "#818cf8", goal: "#fbbf24" }
    : { grid: "#f1f5f9", axis: "#94a3b8", ttFg: "#475569", ttBg: "#ffffff", ttBorder: "#e2e8f0", cursor: "#f8fafc", actual: "#4f46e5", goal: "#ea580c" };

  // 달성율 색: ≥100 녹 · 80~99 주황 · <80 적 (사용자 정의)
  const rc = (v: number) => (v >= 100 ? (dark ? "#4ade80" : "#16a34a") : v >= 80 ? (dark ? "#fbbf24" : "#d97706") : (dark ? "#f87171" : "#dc2626"));

  const storeOpts = useMemo(() => {
    const pool = types.length ? meta.stores.filter((s) => types.includes(s.shop_type)) : meta.stores;
    return pool.map((s) => s.store_name);
  }, [meta.stores, types]);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (month) p.set("month", month);
    types.forEach((t) => p.append("type", t));
    stores.forEach((s) => p.append("store", s));
    return "?" + p.toString();
  }, [month, types, stores]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.target(qs)
      .then((r) => { if (alive) { setD(r); if (!month && r?.month) setMonth(r.month); } })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const rateCol = (field: string, header: string, w = 92) =>
    colNum(field, header, "num", { minWidth: w, valueFormatter: (p: any) => (p.value == null ? "—" : (p.value).toFixed(1) + "%"), cellStyle: (p: any) => ({ textAlign: "right", fontWeight: 700, color: rc(p.value || 0) }) });

  const cols = useMemo(() => [
    colText("shop_type", "채널", { pinned: "left", minWidth: 92, valueGetter: (p: any) => (p.data?.__muTotal ? "" : p.data?.shop_type) }),
    colText("store_name", "매장명", { pinned: "left", minWidth: 178, cellRenderer: (p: any) => (p.data?.__muTotal ? "합계" : (<span><span style={{ color: rc(p.data?.rate_mtd ?? 0), marginRight: 6 }}>●</span>{p.data?.store_name}</span>)) }),
    colNum("pm_actual", "전월실적", "compact", { minWidth: 100 }),
    colNum("goal_full", "월목표", "compact", { minWidth: 100 }),
    colNum("proj", "예상마감금액", "compact", { minWidth: 112 }),
    rateCol("proj_rate", "예상달성율", 100),
    colNum("goal_mtd", "당월누계목표", "compact", { minWidth: 114 }),
    colNum("actual_mtd", "당월누계실적", "compact", { minWidth: 114 }),
    rateCol("rate_mtd", "누계달성율", 100),
    colNum("goal_day", "일목표", "compact", { minWidth: 96 }),
    colNum("actual_day", "전일실적", "compact", { minWidth: 96 }),
    rateCol("rate_day", "전일달성율", 100),
    colNum("py_actual", "전년동월실적", "compact", { minWidth: 112 }),
  ], [dark]); // eslint-disable-line react-hooks/exhaustive-deps

  // 합계를 pinned 대신 rowData 최상단 행(__muTotal)으로 — 필터 시 즉시 갱신(pinnedTopRowData 반응성 이슈 회피).
  const gridRows = useMemo(() => {
    const st = d?.stores || [];
    const t = d?.totals;
    return t ? [{ __muTotal: true, shop_type: "", store_name: "합계", ...t }, ...st] : st;
  }, [d]);

  const T = d?.totals || {};
  const activeFilter = types.length + stores.length > 0;
  const Dot = ({ c }: { c: string }) => <span style={{ color: c }}>●</span>;

  return (
    <div className="space-y-5">
      {/* 컨트롤: 월 · 채널 · 매장 */}
      <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">월</div>
          <div className="flex flex-wrap gap-1.5">
            {(d?.months || []).slice(0, 12).map((m: string) => (
              <Chip key={m} active={(month || d?.month) === m} onClick={() => setMonth(m)}>{m}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">채널</div>
          <div className="flex flex-wrap gap-1.5">
            {meta.shop_types.map((t) => (
              <Chip key={t} active={types.includes(t)} onClick={() => {
                const nt = types.includes(t) ? types.filter((x) => x !== t) : [...types, t];
                const pool = nt.length ? meta.stores.filter((s) => nt.includes(s.shop_type)).map((s) => s.store_name) : meta.stores.map((s) => s.store_name);
                setTypes(nt); setStores(stores.filter((s) => pool.includes(s)));
              }}>{t}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">매장</div>
          <MultiSelect label="매장" options={storeOpts} value={stores} onChange={setStores} />
        </div>
        {activeFilter && (
          <button onClick={() => { setTypes([]); setStores([]); }} className="self-end rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">필터 초기화</button>
        )}
        {loading && <Spinner className="mb-1 h-4 w-4" />}
      </CardBody></Card>

      {d?.month && (
        <p className="-mt-2 text-xs text-slate-400 dark:text-slate-400">
          {d.month} · 목표(gspread) vs 실판매(MOSS) · 전일 = 최근 완료 실적일({d.last_actual ? String(d.last_actual).slice(5) : "—"}, 진행 중인 당일 제외) ·누계·예상 = MTD({d.elapsed}/{d.total_days}일) · 예상마감 = 당월누계÷경과일×당월일수 · 오프라인 에디토리얼 매장만
        </p>
      )}

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      {!d ? (
        <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400 dark:text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">목표 데이터 불러오는 중…</span></div>
      ) : !d.available ? (
        <div className="p-10 text-center text-sm text-slate-400 dark:text-slate-400">목표 데이터(target_daily)가 아직 캐시에 없습니다. 다음 전체 갱신(mode=full) 후 표시됩니다.</div>
      ) : (
        <>
          {/* ── 매장별 목표 대비 실적 (상단) ── */}
          <Card><CardBody>
            <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
              <div>
                <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">매장별 목표 대비 실적</h3>
                <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">{d.month} · 누계달성율 기준 상태 · 합계 고정 · 가로 스크롤·정렬</p>
              </div>
              <div className="flex items-center gap-3 text-xs text-slate-500 dark:text-slate-400">
                <span><Dot c={rc(100)} /> ≥100%</span>
                <span><Dot c={rc(80)} /> 80~99%</span>
                <span><Dot c={rc(0)} /> &lt;80%</span>
              </div>
            </div>
            <DataGrid rows={gridRows} columns={cols} dark={dark} height={Math.min(900, 92 + ((d.stores?.length || 0) + 1) * 34)} getRowClass={(p: any) => (p.data?.__muTotal ? "mu-total" : "")} />
          </CardBody></Card>

          {/* ── 일별 목표 vs 실적 (달성율·예상달성율 우측 상단) ── */}
          <Card><CardBody>
            <SectionTitle
              title="일별 목표 vs 실적"
              sub={`${d.month} · 막대=실적(GMV) · 선=일 목표 · 달성율 = 1일~전일 실적합 ÷ 목표합`}
              right={
                <div className="flex items-center gap-5">
                  <div className="text-right">
                    <div className="text-[11px] font-medium text-slate-400 dark:text-slate-400">월 목표</div>
                    <div className="text-lg font-bold tabular-nums text-slate-700 dark:text-slate-200">{num(T.goal_full || 0)}</div>
                  </div>
                  <div className="text-right">
                    <div className="text-[11px] font-medium text-slate-400 dark:text-slate-400">달성율 (당월누계)</div>
                    <div className="tabular-nums" style={{ color: rc(T.rate_mtd || 0) }}>
                      <span className="text-sm font-semibold">{num(T.actual_mtd || 0)}</span>
                      <span className="ml-1.5 text-xl font-bold">{(T.rate_mtd || 0).toFixed(1)}%</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-[11px] font-medium text-slate-400 dark:text-slate-400">예상 달성율 (예상마감)</div>
                    <div className="tabular-nums" style={{ color: rc(T.proj_rate || 0) }}>
                      <span className="text-sm font-semibold">{num(T.proj || 0)}</span>
                      <span className="ml-1.5 text-xl font-bold">{(T.proj_rate || 0).toFixed(1)}%</span>
                    </div>
                  </div>
                </div>
              }
            />
            {(d.daily || []).length === 0 ? (
              <div className="flex h-[300px] items-center justify-center text-sm text-slate-400 dark:text-slate-400">데이터 없음</div>
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <ComposedChart data={d.daily} margin={{ left: 8, right: 8, top: 16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
                  <XAxis dataKey="d" tickFormatter={(v: string) => String(v).slice(5)} tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} minTickGap={20} />
                  <YAxis tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={compact} width={48} />
                  <Tooltip formatter={(v: any, n: any) => [v == null ? "—" : won(v as number), n]} labelFormatter={(l: any) => String(l)}
                    contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }}
                    itemStyle={{ color: C.ttFg }} labelStyle={{ color: C.ttFg }} cursor={{ fill: C.cursor }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} formatter={(value: any) => <span style={{ color: C.ttFg }}>{value}</span>} />
                  <Bar dataKey="actual" name="실적" fill={C.actual} radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  <Line type="monotone" dataKey="goal" name="목표" stroke={C.goal} strokeWidth={2.5} dot={false} isAnimationActive={false} />
                </ComposedChart>
              </ResponsiveContainer>
            )}
          </CardBody></Card>
        </>
      )}
    </div>
  );
}
