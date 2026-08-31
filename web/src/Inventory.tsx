import { useEffect, useMemo, useState } from "react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, LabelList, Cell, PieChart, Pie } from "recharts";
import { Boxes, Warehouse, Package, Tags, Download } from "lucide-react";
import { api, num, getToken, toQuery, type Filters } from "./lib";
import { Card, CardBody, SectionTitle, Spinner, FitText } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";
import { CatTick } from "./chartlabels";

type Meta = { shop_types: string[]; business_types: string[] };

function Kpi({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <Card><CardBody className="p-4">
      <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400"><span className="shrink-0">{icon}</span><span className="line-clamp-2 min-w-0 text-xs font-medium leading-tight">{label}</span></div>
      <div className="mt-2 truncate text-xl font-bold tabular-nums tracking-tight text-slate-900 md:text-2xl dark:text-slate-50"><FitText>{value}</FitText></div>
    </CardBody></Card>
  );
}

const PIE_COLORS = ["#6366f1", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#06b6d4", "#ef4444", "#84cc16", "#a855f7", "#94a3b8"];

// 재고/매출 배수(재고비중÷SOB) 색상: >1.5 과다(빨강) · <0.7 건전/부족(초록) · 그 외 중립. null(매출0)=과다.
function overStyle(v: number | null | undefined, dark: boolean): any {
  const up = dark ? "#f87171" : "#dc2626", down = dark ? "#4ade80" : "#16a34a", neu = dark ? "#cbd5e1" : "#475569";
  if (v == null) return { textAlign: "right", color: up, fontWeight: 700 };
  const c = v >= 1.5 ? up : v <= 0.7 ? down : neu;
  return { textAlign: "right", color: c, fontWeight: v >= 1.5 || v <= 0.7 ? 700 : 500 };
}

// 조각 안 비중(%) 라벨 — 마우스 없이 바로 표시. 5% 미만은 겹침 방지로 생략.
const pieLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }: any) => {
  if (!percent || percent < 0.05) return null;
  const RAD = Math.PI / 180;
  const r = innerRadius + (outerRadius - innerRadius) * 0.5;
  const x = cx + r * Math.cos(-midAngle * RAD);
  const y = cy + r * Math.sin(-midAngle * RAD);
  return (
    <text x={x} y={y} fill="#fff" fontSize={11} fontWeight={700} textAnchor="middle" dominantBaseline="central">
      {(percent * 100).toFixed(0)}%
    </text>
  );
};

// 카테고리별 재고구성 원형그래프 (총재고=점재고+허브)
function CatPie({ title, data, C }: { title: string; data: { name: string; value: number }[]; C: any }) {
  const total = data.reduce((a, x) => a + (x.value || 0), 0);
  return (
    <Card><CardBody>
      <SectionTitle title={title} sub={`점재고 ${num(total)}`} />
      {!data.length ? (
        <div className="flex h-[260px] items-center justify-center text-sm text-slate-400">데이터 없음</div>
      ) : (
        <ResponsiveContainer width="100%" height={270}>
          <PieChart>
            <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="46%" outerRadius={82} innerRadius={44} paddingAngle={1} isAnimationActive={false} stroke="none" label={pieLabel} labelLine={false}>
              {data.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
            </Pie>
            <Tooltip
              formatter={(v: any, n: any) => [`${num(v as number)} (${total ? (((v as number) / total) * 100).toFixed(1) : 0}%)`, n]}
              contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} />
            <Legend wrapperStyle={{ fontSize: 11 }} iconSize={9} />
          </PieChart>
        </ResponsiveContainer>
      )}
    </CardBody></Card>
  );
}

async function invCsv(qs: string) {
  const r = await fetch("/api/inventory.csv" + qs, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!r.ok) { alert("CSV 실패 (" + r.status + ")"); return; }
  const blob = await r.blob();
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "offline_inventory_long.csv"; a.click(); URL.revokeObjectURL(a.href);
}

export default function Inventory({ meta, dark, filters, onPick }: { meta: Meta; dark: boolean; filters: Filters; onPick?: (k: keyof Filters, v: string) => void }) {
  void meta;
  const activeStores = filters.store || [];
  const storeOp = (name: string) => (activeStores.length ? (activeStores.includes(name) ? 1 : 0.28) : 1);
  const pickStore = onPick ? (dd: any) => { const nm = dd?.name ?? dd?.payload?.name; if (nm != null) onPick("store", String(nm)); } : undefined;
  const pickBrand = onPick ? (dd: any) => { const nm = dd?.name ?? dd?.payload?.name; if (nm != null) onPick("brand", String(nm)); } : undefined;
  const C = dark
    ? { grid: "#1e293b", axis: "#94a3b8", ttFg: "#cbd5e1", ttBg: "#1e293b", ttBorder: "#475569", cursor: "rgba(129,140,248,0.14)", wt: "#818cf8", mi: "#a78bfa", etc: "#94a3b8" }
    : { grid: "#f1f5f9", axis: "#94a3b8", ttFg: "#475569", ttBg: "#ffffff", ttBorder: "#e2e8f0", cursor: "#f8fafc", wt: "#4f46e5", mi: "#7c3aed", etc: "#94a3b8" };
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const qs = useMemo(() => toQuery(filters, { withDate: false }), [filters]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.inventory(qs).then((r) => alive && setD(r)).catch((e) => alive && setError(e.message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, reloadKey]);

  const hubcols: string[] = d?.hubcols ?? [];
  const storeCols: string[] = d?.store_cols ?? [];
  const invCols = useMemo(() => [
    colText("brand_nm", "브랜드", { pinned: "left", minWidth: 110 }),
    colText("goods_nm", "상품", { pinned: "left", minWidth: 200 }),
    colNum("goods_no", "상품번호", "int", { minWidth: 90, valueFormatter: (p: any) => String(p.value ?? "") }),
    colText("style_no", "스타일넘버", { minWidth: 118 }),
    colText("goods_opt", "옵션", { minWidth: 80 }),
    colText("business_type", "사업구분", { minWidth: 78 }),
    colText("cat_top", "최상위카테", { minWidth: 92 }),
    colText("cat_large", "대카테", { minWidth: 92 }),
    colText("cat_medium", "중카테", { minWidth: 92 }),
    colNum("normal_price", "정상가", "num", { minWidth: 84 }),
    colNum("sale_price", "판매가", "num", { minWidth: 84 }),
    colNum("점재고합계", "점재고합계", "num", { pinned: "left", minWidth: 92 }),
    ...storeCols.map((s) => colNum(s, s, "num", { minWidth: 86 })),   // 점별(매장) 컬럼
    ...hubcols.map((h) => colNum(h, h, "num")),
    colNum("허브합계", "허브합계", "num"),
  ], [storeCols.join(","), hubcols.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  // 브랜드별 점재고·GMV·SOB 표 (그래프 대체) — 재고 과다 판단
  const brandRows: any[] = d?.brand_stock ?? [];
  const brandTotal = useMemo(() => {
    if (!brandRows.length) return [];
    const s = (k: string) => brandRows.reduce((a: number, r: any) => a + (Number(r[k]) || 0), 0);
    const ss = s("share"), gs = s("gmv_share");
    return [{ name: "합계(상위)", total: s("total"), share: ss, gmv: s("gmv"), gmv_share: gs, over_index: gs ? +(ss / gs).toFixed(2) : null }];
  }, [brandRows]);
  const brandTblCols = useMemo(() => [
    colText("name", "브랜드", {
      pinned: "left", minWidth: 150,
      cellStyle: (p: any): any => (p.data?.name && p.data?.name !== "합계(상위)" && onPick ? { cursor: "pointer", color: dark ? "#a5b4fc" : "#4f46e5", fontWeight: 600 } : { fontWeight: 700 }),
    }),
    colNum("total", "점재고", "num", { minWidth: 90 }),
    colNum("share", "재고비중", "num", { minWidth: 86, valueFormatter: (p: any) => (p.value ?? 0).toFixed(1) + "%" }),
    colNum("gmv", "GMV(28일)", "compact", { minWidth: 100 }),
    colNum("gmv_share", "SOB", "num", { minWidth: 82, headerTooltip: "Share of Business = 매출비중(최근28일 전체 GMV 대비)", valueFormatter: (p: any) => (p.value ?? 0).toFixed(1) + "%" }),
    colNum("over_index", "재고/매출배수", "num", {
      minWidth: 112, headerTooltip: "재고비중 ÷ SOB · >1 재고과다(느린 회전) · <1 재고부족(빠른 회전) · ∞=최근 매출 없음",
      valueFormatter: (p: any) => (p.value == null ? "∞" : (p.value as number).toFixed(2) + "×"),
      cellStyle: (p: any): any => overStyle(p.value, dark),
    }),
  ], [dark, onPick]);
  const brandGridKey = useMemo(() => `${brandRows.length}|${Math.round(brandTotal[0]?.total || 0)}|${Math.round(brandTotal[0]?.gmv || 0)}`, [brandRows.length, brandTotal]);

  // 상품옵션별 재고 표 합계행(표시 행 합) — 필터 반응(remount key). 재고 수량 컬럼만 합산.
  const invTotal = useMemo(() => {
    const rr = d?.rows ?? [];
    if (!rr.length) return [];
    const numCols = ["점재고합계", "허브합계", ...storeCols, ...hubcols];
    const t: any = { brand_nm: "합계", goods_nm: "", goods_no: "", style_no: "", goods_opt: "", business_type: "", cat_top: "", cat_large: "", cat_medium: "" };
    for (const c of numCols) t[c] = rr.reduce((a: number, r: any) => a + (Number(r[c]) || 0), 0);
    return [t];
  }, [d, storeCols, hubcols]);
  const invGridKey = useMemo(() => `${(d?.rows?.length) || 0}|${Math.round(invTotal[0]?.["점재고합계"] || 0)}|${Math.round(invTotal[0]?.["허브합계"] || 0)}`, [d, invTotal]);
  const onBrandClick = (e: any) => { if (e?.node?.rowPinned || !pickBrand) return; const nm = e?.data?.name; if (nm && nm !== "합계(상위)") pickBrand({ name: nm }); };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-400 dark:text-slate-400">최신 스냅샷 · 상품·옵션(barcode) 단위 · 창고: MFS / 허브1000 / 허브1700 · 기간 필터 미적용 (매장타입·매장으로 보이는 점재고 결정)</p>
        {loading && <Spinner className="h-4 w-4" />}
      </div>

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      {!d ? (
        <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">재고 불러오는 중…</span></div>
      ) : d.empty ? (
        <div className="p-10 text-center text-sm text-slate-400">조건에 해당하는 재고가 없습니다.</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi icon={<Boxes size={16} />} label="점재고 합계 (선택 매장)" value={num(d.kpis.jaego)} />
            <Kpi icon={<Warehouse size={16} />} label="창고(허브) 합계" value={num(d.kpis.hub)} />
            <Kpi icon={<Package size={16} />} label="옵션 수 (barcode)" value={num(d.kpis.options)} />
            <Kpi icon={<Tags size={16} />} label="상품 수 (goods)" value={num(d.kpis.goods)} />
          </div>

          {(d.cats?.cat_top?.length || d.cats?.cat_large?.length || d.cats?.cat_medium?.length) ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <CatPie title="최상위카테고리 점재고 구성" data={d.cats?.cat_top ?? []} C={C} />
              <CatPie title="대카테고리 점재고 구성" data={d.cats?.cat_large ?? []} C={C} />
              <CatPie title="중카테고리 점재고 구성" data={d.cats?.cat_medium ?? []} C={C} />
            </div>
          ) : null}

          <Card><CardBody>
            <SectionTitle title="매장별 재고수량" sub={onPick ? "사업구분(위탁/매입) 누적 · 막대 클릭=필터" : "사업구분(위탁/매입) 누적"} />
            {d.stores.length === 0 ? <div className="flex h-[400px] items-center justify-center text-sm text-slate-400">표시할 매장 점재고가 없습니다.</div> : (
              <ResponsiveContainer width="100%" height={430}>
                <BarChart data={d.stores} layout="vertical" margin={{ left: 8, right: 52, top: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.grid} horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11, fill: C.axis }} tickLine={false} axisLine={false} tickFormatter={(v) => num(v)} />
                  <YAxis type="category" dataKey="name" width={140} interval={0} tick={<CatTick fill={C.ttFg} width={130} />} tickLine={false} axisLine={false} />
                  <Tooltip formatter={(v: any, n: any, item: any) => { const tot = item?.payload?.total || 0; return [`${num(v as number)} (${tot ? ((v / tot) * 100).toFixed(1) : 0}%)`, n]; }} contentStyle={{ borderRadius: 12, background: C.ttBg, color: C.ttFg, border: "1px solid " + C.ttBorder, fontSize: 13 }} cursor={{ fill: C.cursor }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="위탁" stackId="a" fill={C.wt} onClick={pickStore} cursor={onPick ? "pointer" : undefined} isAnimationActive={false}>
                    {onPick && d.stores.map((s: any, i: number) => <Cell key={i} fill={C.wt} fillOpacity={storeOp(s.name)} />)}
                  </Bar>
                  <Bar dataKey="매입" stackId="a" fill={C.mi} onClick={pickStore} cursor={onPick ? "pointer" : undefined} isAnimationActive={false}>
                    {onPick && d.stores.map((s: any, i: number) => <Cell key={i} fill={C.mi} fillOpacity={storeOp(s.name)} />)}
                  </Bar>
                  <Bar dataKey="기타" stackId="a" fill={C.etc} radius={[0, 4, 4, 0]} onClick={pickStore} cursor={onPick ? "pointer" : undefined} isAnimationActive={false}>
                    {onPick && d.stores.map((s: any, i: number) => <Cell key={i} fill={C.etc} fillOpacity={storeOp(s.name)} />)}
                    <LabelList valueAccessor={(e: any) => e?.payload?.total} position="right" formatter={(v: any) => num(v as number)} fontSize={11} fill={C.ttFg} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardBody></Card>

          <Card><CardBody>
            <SectionTitle title="브랜드별 점재고 · GMV · 재고 과다" sub={`상위 ${num(brandRows.length)}개 · 재고=현재 점재고(선택 매장) · GMV/SOB=최근 28일${d.brand_gmv_window ? ` (${d.brand_gmv_window})` : ""} · 재고/매출배수>1=재고과다${onPick ? " · 행 클릭=브랜드 필터" : ""}`} />
            {brandRows.length === 0 ? <div className="flex h-[200px] items-center justify-center text-sm text-slate-400">표시할 브랜드 점재고가 없습니다.</div> : (
              <DataGrid key={brandGridKey} rows={brandRows} columns={brandTblCols} dark={dark} pinnedTop={brandTotal}
                onCellClicked={onBrandClick} height={Math.min(720, 96 + (brandRows.length + 1) * 34)} />
            )}
          </CardBody></Card>

          <Card><CardBody>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">상품옵션별 재고</h3>
                <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">총 {num(d.kpis.options)}개 옵션 · 화면 상위 {num(d.rows.length)}개(점재고합계↓ 허브합계↓) · 브랜드/상품/점재고합계 좌측 고정 · 점별 매장 {num(storeCols.length)}개 컬럼 · 전체는 CSV</p>
              </div>
              <button onClick={() => invCsv(qs)} className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 dark:hover:bg-indigo-500">
                <Download size={14} /> CSV (long·매장/창고별)
              </button>
            </div>
            <DataGrid key={invGridKey} rows={d.rows} columns={invCols} dark={dark} height={560} pinnedTop={invTotal} />
          </CardBody></Card>
        </>
      )}
    </div>
  );
}
