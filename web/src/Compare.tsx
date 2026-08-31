import { useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import { api, won, num, pct, toQuery, daysBeforeISO, prevRange, type Filters } from "./lib";
import { Card, CardBody, SectionTitle, Chip, Spinner, FitText } from "./ui";
import DataGrid, { colText, colNum, colRatio } from "./Grid";

type Meta = { date_min: string; date_max: string; shop_types: string[]; business_types: string[] };

const DATE_INPUT =
  "rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:[color-scheme:dark]";

function ratioCls(v: string): string {
  if (typeof v !== "string") return "text-slate-400";
  if (v.includes("%")) return v.includes("-") ? "text-rose-600 dark:text-rose-400 font-semibold" : "text-emerald-600 dark:text-emerald-400 font-semibold";
  if (v.includes("신규")) return "text-blue-600 dark:text-blue-400 font-semibold";
  return "text-slate-400 dark:text-slate-400";
}

// ── 구간 A/B 비교용 병합 유틸 ─────────────────────────────
const AB_COLS = ["A구간", "B구간", "증감"];
const AB_RC = ["증감"];
function abRatio(a: number, b: number): string {
  if (!b) return a > 0 ? "신규" : "-";
  const p = ((a - b) / b) * 100;
  return (p > 0 ? "+" : "") + p.toFixed(1) + "%";
}
// store/brand/category: /api/by 결과 [{name, gmv}] 두 구간을 name으로 병합
function mergeName(aRows: any[], bRows: any[]) {
  const m = new Map<string, any>();
  for (const r of aRows || []) m.set(r.name, { name: r.name, "A구간": r.gmv || 0, "B구간": 0 });
  for (const r of bRows || []) {
    const e = m.get(r.name) || { name: r.name, "A구간": 0, "B구간": 0 };
    e["B구간"] = r.gmv || 0;
    m.set(r.name, e);
  }
  return finalizeAB([...m.values()]);
}
// goods: /api/sales/goods 결과 rows를 goods_no로 병합
function mergeGoods(aRows: any[], bRows: any[]) {
  const m = new Map<any, any>();
  const base = (r: any) => ({ brand: r.brand_nm, goods_no: r.goods_no, goods_nm: r.goods_nm, cat_medium: r.cat_medium });
  for (const r of aRows || []) m.set(r.goods_no, { ...base(r), "A구간": r.gmv || 0, "B구간": 0 });
  for (const r of bRows || []) {
    const e = m.get(r.goods_no) || { ...base(r), "A구간": 0, "B구간": 0 };
    e["B구간"] = r.gmv || 0;
    m.set(r.goods_no, e);
  }
  return finalizeAB([...m.values()]);
}
function finalizeAB(rows: any[]) {
  for (const r of rows) r["증감"] = abRatio(r["A구간"], r["B구간"]);
  rows.sort((x, y) => y["A구간"] - x["A구간"] || y["B구간"] - x["B구간"]);
  const sa = rows.reduce((s, r) => s + r["A구간"], 0);
  const sb = rows.reduce((s, r) => s + r["B구간"], 0);
  return { total: { _total: true, "A구간": sa, "B구간": sb, "증감": abRatio(sa, sb) }, rows };
}

type LabelCol = { key: string; label: string; pinWidth?: number };

const hdr = (c: string) => c.replace("기준일gmv", "기준일").replace("gmv", "").replace("wtd", " 전기");

function CmpTable({ title, sub, table, labelCols, cols, ratioCols, csvName, height, dark, onRowPick, colHeaders }: {
  title: string; sub: string; table: any; labelCols: LabelCol[]; cols: string[]; ratioCols: string[]; csvName: string; height: number; dark: boolean;
  onRowPick?: (value: string) => void;
  colHeaders?: Record<string, string>;
}) {
  if (!table) return (
    <Card><CardBody><SectionTitle title={title} sub={sub} /><div className="py-8 text-center text-sm text-slate-400">기준일에 해당하는 데이터가 없습니다.</div></CardBody></Card>
  );
  const rset = new Set(ratioCols);
  const csv = () => {
    const esc = (v: any) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const head = [...labelCols.map((c) => c.label), ...cols.map((c) => colHeaders?.[c] ?? c)];
    const lines = [head.join(",")];
    for (const r of [table.total, ...table.rows]) {
      const lbl = labelCols.map((c, i) => (r._total ? (i === 0 ? "합계" : "") : r[c.key]));
      const vals = cols.map((c) => (rset.has(c) ? r[c] : Math.round(r[c] ?? 0)));
      lines.push([...lbl, ...vals].map(esc).join(","));
    }
    const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8;" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = csvName; a.click(); URL.revokeObjectURL(a.href);
  };
  const columns = [
    ...labelCols.map((lc, i) =>
      colText(lc.key, lc.label, {
        pinned: "left", minWidth: lc.pinWidth ?? 110,
        ...(i === 0 ? { valueGetter: (p: any) => (p.node?.rowPinned === "top" ? "합계" : p.data?.[lc.key]) } : {}),
      })
    ),
    ...cols.map((c) => (rset.has(c) ? colRatio(c, colHeaders?.[c] ?? hdr(c)) : colNum(c, colHeaders?.[c] ?? hdr(c), "compact"))),
  ];
  const labelKey = labelCols[0]?.key;
  const cellClick = onRowPick
    ? (e: any) => {
        if (e?.node?.rowPinned) return;                          // 합계행 제외
        if (e?.column?.getColId?.() !== labelKey) return;        // 첫 라벨 칼럼만
        const v = e?.data?.[labelKey];
        if (v != null && String(v) !== "") onRowPick(String(v));
      }
    : undefined;
  return (
    <Card><CardBody>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div><h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">{title}</h3><p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">{sub} · 합계 고정 · 열 이동/정렬{onRowPick ? " · 첫 칼럼 클릭=필터" : ""}</p></div>
        <button onClick={csv} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"><Download size={14} /> CSV</button>
      </div>
      <DataGrid key={JSON.stringify(table.total)} rows={table.rows} columns={columns} dark={dark} height={height} pinnedTop={[table.total]} onCellClicked={cellClick} />
    </CardBody></Card>
  );
}

function SummaryKpi({ label, value, delta }: { label: string; value: string; delta: string }) {
  return (
    <Card><CardBody className="p-4">
      <div className="line-clamp-2 text-xs font-medium leading-tight text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-2 truncate text-xl font-bold tabular-nums tracking-tight text-slate-900 md:text-2xl dark:text-slate-50" title={value}><FitText>{value}</FitText></div>
      <div className={`mt-1 text-xs ${ratioCls(delta)}`}>{delta}</div>
    </CardBody></Card>
  );
}

export default function Compare({ meta, filters, dark, onPick }: { meta: Meta; filters: Filters; dark: boolean; onPick?: (k: keyof Filters, v: string) => void }) {
  const dmax = meta.date_max?.slice(0, 10) || "";
  const dmin = meta.date_min?.slice(0, 10) || "";
  const [mode, setMode] = useState<"ref" | "ab">("ref"); // 기준일 비교 / 구간 A/B 비교
  const [ref, setRef] = useState<string>(dmax);
  const [clv, setClv] = useState<string>("대카테");
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  // ── 구간 A/B 상태 (초기: A=최근7일, B=그 직전7일) ──
  const initAFrom = dmax ? daysBeforeISO(dmax, 6) : "";
  const initB = dmax ? prevRange(initAFrom, dmax) : { from: "", to: "" };
  const [aFrom, setAFrom] = useState(initAFrom);
  const [aTo, setATo] = useState(dmax);
  const [bFrom, setBFrom] = useState(initB.from);
  const [bTo, setBTo] = useState(initB.to);
  const [ab, setAb] = useState<any>(null);
  const [abLoading, setAbLoading] = useState(false);

  const clvKey: keyof Filters = clv === "최상위" ? "cat_top" : clv === "대카테" ? "cat_large" : "cat_medium";

  // 기준일(ref) 비교 조회
  const qs = useMemo(() => {
    const fq = toQuery(filters, { withDate: false }); // biz/type/store/brand/cat/md/goods (날짜 제외)
    const p = new URLSearchParams(fq.startsWith("?") ? fq.slice(1) : fq);
    if (ref) p.set("ref", ref);
    if (clv) p.set("clv", clv);
    return "?" + p.toString();
  }, [ref, clv, filters]);

  useEffect(() => {
    if (mode !== "ref") return;
    let alive = true;
    setLoading(true); setError("");
    api.compare(qs).then((r) => alive && setD(r)).catch((e) => alive && setError(e.message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [mode, qs, reloadKey]);

  // 구간 A/B 비교 조회 (각 구간을 summary/by/goods로 2회씩 조회 후 병합)
  useEffect(() => {
    if (mode !== "ab" || !aFrom || !aTo || !bFrom || !bTo) return;
    const base = toQuery(filters, { withDate: false });
    const withRange = (from: string, to: string) => {
      const p = new URLSearchParams(base.startsWith("?") ? base.slice(1) : base);
      p.set("date_from", from); p.set("date_to", to);
      return "?" + p.toString();
    };
    const qA = withRange(aFrom, aTo), qB = withRange(bFrom, bTo);
    const catDim = clvKey;
    let alive = true;
    setAbLoading(true); setError("");
    Promise.all([
      api.summary(qA), api.summary(qB),
      api.by("store", qA, 300), api.by("store", qB, 300),
      api.by(catDim, qA, 300), api.by(catDim, qB, 300),
      api.by("brand", qA, 300), api.by("brand", qB, 300),
      api.salesGoods(qA, 400), api.salesGoods(qB, 400),
    ]).then(([sa, sb, stA, stB, caA, caB, brA, brB, gA, gB]: any[]) => {
      if (!alive) return;
      setAb({
        sa, sb,
        store: mergeName(stA, stB),
        category: mergeName(caA, caB),
        brand: mergeName(brA, brB),
        goods: mergeGoods(gA?.rows, gB?.rows),
      });
    }).catch((e) => alive && setError(e.message)).finally(() => alive && setAbLoading(false));
    return () => { alive = false; };
  }, [mode, aFrom, aTo, bFrom, bTo, clvKey, filters, reloadKey]);

  const cols: string[] = d?.cols ?? [];
  const rc: string[] = d?.ratio_cols ?? [];
  const kd = (a: number, b: number) => (b ? "B대비 " + pct(((a - b) / b) * 100) : a > 0 ? "B대비 신규" : "—");
  const abSub = `A ${aFrom}~${aTo} vs B ${bFrom}~${bTo}`;
  const shortD = (s: string) => (s ? s.slice(5).replace("-", "/") : s); // 2026-06-25 → 06/25
  const abHeaders: Record<string, string> = {
    "A구간": `A ${shortD(aFrom)}~${shortD(aTo)}`,
    "B구간": `B ${shortD(bFrom)}~${shortD(bTo)}`,
    "증감": "증감",
  };

  return (
    <div className="space-y-5">
      {/* 모드 토글 */}
      <div className="flex gap-1.5">
        <Chip active={mode === "ref"} onClick={() => setMode("ref")}>기준일 비교</Chip>
        <Chip active={mode === "ab"} onClick={() => setMode("ab")}>구간 A/B 비교</Chip>
      </div>

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      {mode === "ref" ? (
        <>
          <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">기준일</div>
              <input type="date" value={ref} min={dmin} max={dmax} onChange={(e) => setRef(e.target.value)} className={DATE_INPUT} />
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">카테고리 레벨</div>
              <div className="flex gap-1.5">{["최상위", "대카테", "중카테"].map((c) => <Chip key={c} active={clv === c} onClick={() => setClv(c)}>{c}</Chip>)}</div>
            </div>
            {loading && <Spinner className="mb-1" />}
          </CardBody></Card>

          {d?.info && <p className="-mt-2 text-xs text-slate-400 dark:text-slate-400">기준일 {d.ref} (ISO {d.info.iso_year}-W{String(d.info.iso_week).padStart(2, "0")} {d.info.wday}) · 전월 {d.info.m_from}~{d.ref} vs {d.info.pm_from}~{d.info.pm_to} · 전년 동기(−364) 기준 · 모든 신장율은 동기(같은 경과일 누적) 비교</p>}

          {!d ? (
            <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">비교 데이터 불러오는 중…</span></div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <SummaryKpi label="기준일 GMV" value={won(d.summary["기준일gmv"])} delta={"전일비 " + d.summary_ratio[rc[0]]} />
                <SummaryKpi label="당주 GMV (WTD)" value={won(d.summary["당주gmv"])} delta={"전주비 " + d.summary_ratio[rc[1]]} />
                <SummaryKpi label="당월 GMV (MTD)" value={won(d.summary["당월gmv"])} delta={"전월비 " + d.summary_ratio[rc[2]]} />
                <SummaryKpi label="당년 GMV (YTD)" value={won(d.summary["당년gmv"])} delta={"전년비 " + d.summary_ratio[rc[3]]} />
              </div>

              <CmpTable dark={dark} title="매장별 비교" sub={`기준일(${d.ref}) 매출 발생 매장 · 동기비`} table={d.store} labelCols={[{ key: "name", label: "매장", pinWidth: 150 }]} cols={cols} ratioCols={rc} csvName="compare_store.csv" height={460} onRowPick={onPick ? (v) => onPick("store", v) : undefined} />
              <CmpTable dark={dark} title="카테고리별 비교" sub={`${d.clv} 기준 · 동기비`} table={d.category} labelCols={[{ key: "name", label: "카테고리", pinWidth: 150 }]} cols={cols} ratioCols={rc} csvName="compare_category.csv" height={460} onRowPick={onPick ? (v) => onPick(clvKey, v) : undefined} />
              <CmpTable dark={dark} title="브랜드별 비교" sub={`기준일(${d.ref}) 매출 발생 브랜드 (상위 200) · 동기비`} table={d.brand} labelCols={[{ key: "name", label: "브랜드", pinWidth: 150 }]} cols={cols} ratioCols={rc} csvName="compare_brand.csv" height={460} onRowPick={onPick ? (v) => onPick("brand", v) : undefined} />
              <CmpTable
                dark={dark}
                title="상품별 비교" sub={`기준일(${d.ref}) 매출 상위 300 · 동기비 · 🔵신규=등록 90일 이내`}
                table={d.goods}
                labelCols={[{ key: "brand", label: "브랜드", pinWidth: 120 }, { key: "goods_no", label: "UID", pinWidth: 90 }, { key: "goods_nm", label: "상품", pinWidth: 240 }, { key: "cat_medium", label: "중카테", pinWidth: 110 }]}
                cols={cols} ratioCols={rc} csvName="compare_goods.csv" height={560}
              />
            </>
          )}
        </>
      ) : (
        <>
          <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">A 구간</div>
              <div className="flex items-center gap-2">
                <input type="date" value={aFrom} min={dmin} max={aTo || dmax} onChange={(e) => setAFrom(e.target.value)} className={DATE_INPUT} />
                <span className="text-slate-300 dark:text-slate-500">~</span>
                <input type="date" value={aTo} min={aFrom || dmin} max={dmax} onChange={(e) => setATo(e.target.value)} className={DATE_INPUT} />
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">B 구간 (비교 대상)</div>
              <div className="flex items-center gap-2">
                <input type="date" value={bFrom} min={dmin} max={bTo || dmax} onChange={(e) => setBFrom(e.target.value)} className={DATE_INPUT} />
                <span className="text-slate-300 dark:text-slate-500">~</span>
                <input type="date" value={bTo} min={bFrom || dmin} max={dmax} onChange={(e) => setBTo(e.target.value)} className={DATE_INPUT} />
                <button onClick={() => { const r = prevRange(aFrom, aTo); setBFrom(r.from); setBTo(r.to); }}
                  className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">A 직전동기</button>
              </div>
            </div>
            <div>
              <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">카테고리 레벨</div>
              <div className="flex gap-1.5">{["최상위", "대카테", "중카테"].map((c) => <Chip key={c} active={clv === c} onClick={() => setClv(c)}>{c}</Chip>)}</div>
            </div>
            {abLoading && <Spinner className="mb-1" />}
          </CardBody></Card>

          <p className="-mt-2 text-xs text-slate-400 dark:text-slate-400">A 구간 vs B 구간 GMV 비교 · 증감 = (A−B)/B · 매장/카테/브랜드/상품별 · 그 외 필터(사업구분·매장·브랜드·카테·MD·UID)는 두 구간에 공통 적용</p>

          {!ab ? (
            <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">구간 비교 데이터 불러오는 중…</span></div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <SummaryKpi label="GMV" value={won(ab.sa.gmv)} delta={kd(ab.sa.gmv, ab.sb.gmv)} />
                <SummaryKpi label="순판매수량" value={num(ab.sa.qty)} delta={kd(ab.sa.qty, ab.sb.qty)} />
                <SummaryKpi label="외국인 매출(면세)" value={won(ab.sa.foreign_gmv)} delta={kd(ab.sa.foreign_gmv, ab.sb.foreign_gmv)} />
                <SummaryKpi label="정상가 매출" value={won(ab.sa.normal_amt)} delta={kd(ab.sa.normal_amt, ab.sb.normal_amt)} />
              </div>

              <CmpTable dark={dark} title="매장별 A/B 비교" sub={abSub} table={ab.store} labelCols={[{ key: "name", label: "매장", pinWidth: 150 }]} cols={AB_COLS} ratioCols={AB_RC} colHeaders={abHeaders} csvName="compare_ab_store.csv" height={460} onRowPick={onPick ? (v) => onPick("store", v) : undefined} />
              <CmpTable dark={dark} title="카테고리별 A/B 비교" sub={`${clv} 기준 · ${abSub}`} table={ab.category} labelCols={[{ key: "name", label: "카테고리", pinWidth: 150 }]} cols={AB_COLS} ratioCols={AB_RC} colHeaders={abHeaders} csvName="compare_ab_category.csv" height={460} onRowPick={onPick ? (v) => onPick(clvKey, v) : undefined} />
              <CmpTable dark={dark} title="브랜드별 A/B 비교" sub={`상위 300 · ${abSub}`} table={ab.brand} labelCols={[{ key: "name", label: "브랜드", pinWidth: 150 }]} cols={AB_COLS} ratioCols={AB_RC} colHeaders={abHeaders} csvName="compare_ab_brand.csv" height={460} onRowPick={onPick ? (v) => onPick("brand", v) : undefined} />
              <CmpTable
                dark={dark}
                title="상품별 A/B 비교" sub={`각 구간 상위 400 합집합 · ${abSub}`}
                table={ab.goods}
                labelCols={[{ key: "brand", label: "브랜드", pinWidth: 120 }, { key: "goods_no", label: "UID", pinWidth: 90 }, { key: "goods_nm", label: "상품", pinWidth: 240 }, { key: "cat_medium", label: "중카테", pinWidth: 110 }]}
                cols={AB_COLS} ratioCols={AB_RC} colHeaders={abHeaders} csvName="compare_ab_goods.csv" height={560}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}
