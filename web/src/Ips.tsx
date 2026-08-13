import { useEffect, useMemo, useState } from "react";
import { api, compact } from "./lib";
import { Card, CardBody, Chip, Spinner } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";

// 통합 IPS 탭 — 브랜드×구분(매입/위탁) 단일 뷰. 공통 필터 미적용(독립).
// Phase 1: 재고(전체/매장/물류)+입고 + 4주 판매(수량/GMV/NetTake) + 셀스루·예상일수. CP는 숨김.
// 채널 토글(전체/온라인/오프라인)로 주차별(W0=직전 완료주) 지표 컬럼을 전환.

type Ch = "tot" | "on" | "off";

// 판매율 히트맵(높을수록 초록 → 낮을수록 빨강). 앵커 = 전체 셀스루.
function heatSellThrough(rows: any[], anchor: number, dark: boolean) {
  const vals = rows.filter((r) => r.sell_through != null).map((r) => r.sell_through as number);
  const up = Math.max(1e-6, (vals.length ? Math.max(...vals) : anchor + 1) - anchor);
  const dn = Math.max(1e-6, anchor - (vals.length ? Math.min(...vals) : anchor - 1));
  return colNum("sell_through", "판매율", "num", {
    minWidth: 82,
    valueFormatter: (p: any) => (p.value == null ? "—" : (p.value as number).toFixed(1) + "%"),
    cellStyle: (p: any) => {
      const v = p.value as number | null;
      if (v == null) return { textAlign: "right", color: "var(--ratio-neutral)" };
      let hue: number, t: number;
      if (v >= anchor) { t = Math.min(1, (v - anchor) / up); hue = 95 + 40 * t; }
      else { t = Math.min(1, (anchor - v) / dn); hue = 55 - 55 * t; }
      const [b, s] = dark ? [0.18, 0.42] : [0.12, 0.4];
      return { textAlign: "right", backgroundColor: `hsla(${hue.toFixed(0)},78%,50%,${(b + s * t).toFixed(3)})`, fontWeight: 600, ...(dark ? { color: "#e2e8f0" } : {}) };
    },
  });
}

export default function Ips({ dark }: { dark: boolean }) {
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const [gubun, setGubun] = useState<string[]>([]);   // 매입/위탁
  const [sils, setSils] = useState<string[]>([]);     // 실
  const [ch, setCh] = useState<Ch>("tot");            // 채널(주차 지표 전환)
  const [q, setQ] = useState("");                     // 브랜드 검색

  // 상품 드릴다운
  const [drill, setDrill] = useState<{ brand_code: string; gubun: string; brand_nm: string } | null>(null);
  const [gRows, setGRows] = useState<any[]>([]);
  const [gLoading, setGLoading] = useState(false);

  const openDrill = (e: any) => {
    if (e?.node?.rowPinned) return;
    const r = e?.data;
    if (!r || !r.brand_code || r.brand_nm === "합계") return;
    setDrill({ brand_code: r.brand_code, gubun: r.gubun, brand_nm: r.brand_nm });
    setGLoading(true); setGRows([]);
    api.ipsGoods(r.brand_code, r.gubun)
      .then((d) => setGRows(d?.rows || []))
      .catch(() => setGRows([]))
      .finally(() => setGLoading(false));
  };

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.ips()
      .then((r) => alive && setD(r))
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [reloadKey]);

  const allRows: any[] = d?.rows || [];
  const silOptions = useMemo(() => Array.from(new Set(allRows.map((r) => r.sil).filter(Boolean))).sort(), [allRows]);

  const rows = useMemo(() => {
    const qq = q.trim().toLowerCase();
    return allRows.filter((r) =>
      (!gubun.length || gubun.includes(r.gubun)) &&
      (!sils.length || sils.includes(r.sil)) &&
      (!qq || String(r.brand_nm).toLowerCase().includes(qq) || String(r.brand_code).toLowerCase().includes(qq)));
  }, [allRows, gubun, sils, q]);

  // 필터 반영 합계(파생지표 재계산).
  const total = useMemo(() => {
    if (!rows.length) return [];
    const sum = (k: string) => rows.reduce((a, r) => a + (r[k] || 0), 0);
    const q4 = sum("qty_tot_w0") + sum("qty_tot_w1") + sum("qty_tot_w2") + sum("qty_tot_w3");
    const off4 = sum("qty_off_w0") + sum("qty_off_w1") + sum("qty_off_w2") + sum("qty_off_w3");
    const tc = sum("total_cur");
    const t: any = { brand_nm: "합계", sil: "", gubun: "", com_id: "" };
    ["inbound", "inbound_po", "total_cur", "store_cur", "logi_cur",
     "qty_tot_w0", "qty_tot_w1", "qty_tot_w2", "qty_tot_w3", "qty_on_w0", "qty_on_w1", "qty_on_w2", "qty_on_w3", "qty_off_w0", "qty_off_w1", "qty_off_w2", "qty_off_w3",
     "gmv_tot_w0", "gmv_tot_w1", "gmv_tot_w2", "gmv_tot_w3", "gmv_on_w0", "gmv_on_w1", "gmv_on_w2", "gmv_on_w3", "gmv_off_w0", "gmv_off_w1", "gmv_off_w2", "gmv_off_w3",
     "nt_tot_w0", "nt_tot_w1", "nt_tot_w2", "nt_tot_w3", "nt_on_w0", "nt_on_w1", "nt_on_w2", "nt_on_w3", "nt_off_w0", "nt_off_w1", "nt_off_w2", "nt_off_w3"].forEach((k) => (t[k] = sum(k)));
    t.sell_through = q4 + tc ? +(q4 / (q4 + tc) * 100).toFixed(1) : null;
    t.days_all = q4 ? +(tc / (q4 / 28)).toFixed(1) : null;
    return [t];
  }, [rows]);

  const anchor = total[0]?.sell_through ?? 30;

  const cols = useMemo(() => {
    const c: any[] = [
      colText("sil", "실", { pinned: "left", minWidth: 96 }),
      colText("brand_nm", "브랜드", {
        pinned: "left", minWidth: 150,
        cellStyle: (p: any): any => (p.data?.brand_nm && p.data?.brand_nm !== "합계"
          ? { fontWeight: 600, cursor: "pointer", color: dark ? "#a5b4fc" : "#4f46e5" } : { fontWeight: 700 }),
      }),
      colText("gubun", "구분", {
        minWidth: 62,
        cellStyle: (p: any) => ({ fontWeight: 700, color: p.value === "위탁" ? (dark ? "#60a5fa" : "#2563eb") : (dark ? "#fbbf24" : "#b45309") }),
      }),
      colText("com_id", "업체코드", { minWidth: 100 }),
      // 재고
      colNum("total_cur", "전체재고", "num", { minWidth: 92 }),
      colNum("store_cur", "매장재고", "num", { minWidth: 92 }),
      colNum("logi_cur", "물류재고", "num", { minWidth: 92 }),
      colNum("inbound_po", "입고예정", "num", {
        minWidth: 88,
        headerTooltip: "PLANT 1000 외부업체 발주잔량(open PO, 입고예정일≥오늘). 매입 한정.",
        valueFormatter: (p: any) => (!p.value ? "—" : (p.value as number).toLocaleString("ko-KR")),
      }),
      colNum("inbound", "매장입고(4주)", "num", { minWidth: 100, headerTooltip: "매장 순유입 추정(매장재고 증가 + 오프판매)" }),
      heatSellThrough(rows, anchor, dark),
      colNum("days_all", "예상일수", "num", {
        minWidth: 84,
        valueFormatter: (p: any) => (p.value == null ? "—" : (p.value as number).toLocaleString("ko-KR") + "일"),
      }),
    ];
    // 4주 판매 — 선택 채널의 주차별(W0~W3) + 4주합
    const W = d?.weeks || {};
    const metric = (pfx: string, label: string, fmt: "num" | "compact") => {
      const f = (w: string) => `${pfx}_${ch}_${w}`;
      for (const w of ["w0", "w1", "w2", "w3"]) {
        c.push(colNum(f(w), `${label} ${w.toUpperCase()}`, fmt, {
          minWidth: fmt === "compact" ? 92 : 78,
          headerTooltip: W[w] ? `${w.toUpperCase()} (${W[w]})` : undefined,
          valueFormatter: (p: any) => (fmt === "compact" ? compact(p.value || 0) : (p.value || 0).toLocaleString("ko-KR")),
        }));
      }
      c.push(colNum(`${pfx}_${ch}_sum`, `${label} 4주`, fmt, {
        minWidth: fmt === "compact" ? 96 : 84,
        cellStyle: () => ({ fontWeight: 700 }),
        valueGetter: (p: any) => (p.data ? (p.data[f("w0")] || 0) + (p.data[f("w1")] || 0) + (p.data[f("w2")] || 0) + (p.data[f("w3")] || 0) : 0),
        valueFormatter: (p: any) => (fmt === "compact" ? compact(p.value || 0) : (p.value || 0).toLocaleString("ko-KR")),
      }));
    };
    metric("qty", "수량", "num");
    metric("gmv", "GMV", "compact");
    metric("nt", "NetTake", "compact");
    return c;
  }, [d, rows, ch, dark, anchor]);

  const gridKey = useMemo(
    () => `${ch}|${rows.length}|${Math.round(total[0]?.gmv_tot_w0 || 0)}|${Math.round(total[0]?.total_cur || 0)}`,
    [ch, rows.length, total],
  );

  // 상품 드릴 컬럼 — 채널 토글(전체/온/오프) 반영. 상품 데이터는 4주합(주차 X).
  const gCols = useMemo(() => {
    const suf = ch;
    return [
      colText("goods_nm", "상품", { pinned: "left", minWidth: 220 }),
      colText("product_no", "UID", { minWidth: 92 }),
      colNum("total_cur", "전체재고", "num", { minWidth: 88 }),
      colNum("store_cur", "매장재고", "num", { minWidth: 88 }),
      colNum("logi_cur", "물류재고", "num", { minWidth: 88 }),
      colNum("inbound_po", "입고예정", "num", { minWidth: 84, valueFormatter: (p: any) => (!p.value ? "—" : (p.value as number).toLocaleString("ko-KR")) }),
      colNum(`qty_${suf}`, "판매수량(4주)", "num", { minWidth: 104 }),
      colNum(`gmv_${suf}`, "GMV(4주)", "compact", { minWidth: 100, valueFormatter: (p: any) => compact(p.value || 0) }),
      colNum(`nt_${suf}`, "NetTake(4주)", "compact", { minWidth: 108, valueFormatter: (p: any) => compact(p.value || 0) }),
      colNum("sell_through", "판매율", "num", { minWidth: 78, valueFormatter: (p: any) => (p.value == null ? "—" : (p.value as number).toFixed(1) + "%") }),
      colNum("days_all", "예상일수", "num", { minWidth: 82, valueFormatter: (p: any) => (p.value == null ? "—" : (p.value as number).toLocaleString("ko-KR") + "일") }),
    ];
  }, [ch]);

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) =>
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  return (
    <div className="space-y-5">
      <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400">구분</div>
          <div className="flex gap-1.5">
            {["매입", "위탁"].map((g) => (
              <Chip key={g} active={gubun.includes(g)} onClick={() => toggle(gubun, g, setGubun)}>{g}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400">실</div>
          <div className="flex flex-wrap gap-1.5">
            {silOptions.map((s) => (
              <Chip key={s} active={sils.includes(s)} onClick={() => toggle(sils, s, setSils)}>{s.replace("무신사 ", "")}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400">판매 채널</div>
          <div className="flex gap-1.5">
            {([["tot", "전체"], ["on", "온라인"], ["off", "오프라인"]] as [Ch, string][]).map(([k, l]) => (
              <Chip key={k} active={ch === k} onClick={() => setCh(k)}>{l}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400">브랜드 검색</div>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="브랜드/코드"
            className="w-40 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
        </div>
        {loading && <Spinner className="mb-1 h-4 w-4" />}
      </CardBody></Card>

      <p className="-mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
        <span>
          통합 IPS · 브랜드×구분(매입/위탁) · W0=직전 완료주(월~일)
          {d?.weeks?.w0 && ` · W0 ${d.weeks.w0}`} · 재고=일별 통합(1P/3P/MFS) · 셀스루 = 4주판매 ÷ (4주판매 + 현재고)
          {d?.refreshed_at && ` · 갱신 ${d.refreshed_at.replace("T", " ")}`}
        </span>
      </p>

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      {!d ? (
        <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">IPS 데이터 불러오는 중…</span></div>
      ) : !d.available ? (
        <div className="p-10 text-center text-sm text-slate-400">IPS 데이터(ips 캐시)가 아직 없습니다. 다음 전체 갱신(mode=full) 후 표시됩니다.</div>
      ) : (
        <Card><CardBody>
          {/* 드릴 브레드크럼 */}
          <div className="mb-3 flex items-center gap-2 text-sm">
            <button onClick={() => setDrill(null)}
              className={`rounded-md px-2 py-0.5 font-medium ${drill ? "text-indigo-600 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-950/40" : "text-slate-500 dark:text-slate-300"}`}>
              브랜드별
            </button>
            {drill && <>
              <span className="text-slate-300 dark:text-slate-600">›</span>
              <span className="font-semibold text-slate-700 dark:text-slate-200">{drill.brand_nm}</span>
              <span className="rounded px-1.5 text-xs font-semibold" style={{ color: drill.gubun === "위탁" ? "#2563eb" : "#b45309" }}>{drill.gubun}</span>
              <span className="text-xs text-slate-400">· 상품별</span>
            </>}
            {!drill && <span className="text-xs text-slate-400">· 브랜드 클릭 → 상품 드릴다운</span>}
          </div>

          {drill ? (
            gLoading ? (
              <div className="flex h-60 items-center justify-center gap-3 text-slate-400"><Spinner className="h-6 w-6" /><span className="text-sm">상품 불러오는 중…</span></div>
            ) : !gRows.length ? (
              <div className="p-10 text-center text-sm text-slate-400">해당 브랜드×구분의 상품 데이터가 없습니다. (ips_goods 캐시 필요)</div>
            ) : (
              <>
                <h3 className="mb-2 text-[15px] font-semibold text-slate-800 dark:text-slate-100">
                  {drill.brand_nm} · {drill.gubun} · {ch === "tot" ? "전체" : ch === "on" ? "온라인" : "오프라인"} · {gRows.length}개 상품
                </h3>
                <DataGrid key={`g|${drill.brand_code}|${drill.gubun}|${ch}`} rows={gRows} columns={gCols} dark={dark}
                  height={Math.min(760, 96 + (gRows.length + 1) * 34)} />
              </>
            )
          ) : (
            <>
              <h3 className="mb-1 text-[15px] font-semibold text-slate-800 dark:text-slate-100">
                통합 IPS · {ch === "tot" ? "전체" : ch === "on" ? "온라인" : "오프라인"} 판매 · {rows.length}개 행
              </h3>
              <p className="mb-3 text-xs text-slate-400">GMV 4주합 내림차순 · 합계 고정(필터 반영) · 판매율 = 전체 대비 색상 · 브랜드 클릭 시 상품 드릴</p>
              <DataGrid key={gridKey} rows={rows} columns={cols} dark={dark} pinnedTop={total} onCellClicked={openDrill}
                height={Math.min(880, 96 + (rows.length + 1) * 34)} />
            </>
          )}
        </CardBody></Card>
      )}
    </div>
  );
}
