import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Download, ListTree, Home } from "lucide-react";
import { api, getToken, toQuery, type Filters } from "./lib";
import { Card, CardBody, Spinner } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";

// 드릴 계층: 매장 → 브랜드 → 대카테 → 중카테 → 상품
const SEQ = ["shop", "brand", "cat_large", "cat_medium", "goods"] as const;
type Level = (typeof SEQ)[number];
const LABEL: Record<Level, string> = {
  shop: "매장", brand: "브랜드", cat_large: "대카테고리", cat_medium: "중카테고리", goods: "상품",
};
// 각 레벨의 행을 클릭했을 때 걸리는 필터 키(하위 단계로 전달)
const FILTER_KEY: Record<Exclude<Level, "goods">, string> = {
  shop: "store", brand: "brand", cat_large: "cat_large", cat_medium: "cat_medium",
};

type Crumb = { filterKey: string; value: string };

async function downloadDrillCsv(level: Level, qs: string) {
  const url = "/api/drill.csv" + qs + (qs ? "&" : "?") + "level=" + level;
  const r = await fetch(url, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!r.ok) { alert("CSV 실패 (" + r.status + ")"); return; }
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `drill_${level}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function Drill({ filters, dark }: { meta?: any; dark: boolean; filters: Filters; onPick?: (k: keyof Filters, v: string) => void }) {
  const [path, setPath] = useState<Crumb[]>([]);   // 드릴 경로(상위 선택들)
  const [d, setD] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const level: Level = SEQ[Math.min(path.length, SEQ.length - 1)];
  const isLeaf = level === "goods";

  // 쿼리 = 공통 필터(FilterBar) + 드릴 경로 선택
  const qs = useMemo(() => {
    const base = toQuery(filters);
    const extra = path.map((p) => `${p.filterKey}=${encodeURIComponent(p.value)}`).join("&");
    return base + (extra ? (base ? "&" : "?") + extra : "");
  }, [filters, path]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.drill(level, qs).then((r) => alive && setD(r)).catch((e) => alive && setError(e.message)).finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, level, reloadKey]);

  // 라벨 셀 클릭 → 하위 단계로 드릴 (상품 레벨은 리프)
  const onCellClicked = (e: any) => {
    if (isLeaf) return;
    const colId = e?.column?.getColId?.() ?? e?.colDef?.field;
    if (colId !== "name") return;
    const val = e?.data?.name;
    if (val == null) return;
    setPath((p) => [...p, { filterKey: FILTER_KEY[level as Exclude<Level, "goods">], value: String(val) }]);
  };

  // GMV 비중(판매 탭과 동일): 현재 표시 행들의 GMV 합 대비 비율 + 최대 비중 대비 인디고 히트맵.
  const rawRows = d?.rows ?? [];
  const totalGmv = useMemo(() => rawRows.reduce((s: number, r: any) => s + (r.gmv || 0), 0), [rawRows]);
  const rows = useMemo(() => rawRows.map((r: any) => ({ ...r, gmv_ratio: totalGmv ? (r.gmv / totalGmv) * 100 : 0 })), [rawRows, totalGmv]);
  const maxRatio = useMemo(() => rows.reduce((mx: number, r: any) => Math.max(mx, r.gmv_ratio || 0), 0) || 1, [rows]);

  const columns = useMemo(() => {
    const first = colText("name", LABEL[level], {
      pinned: "left",
      minWidth: isLeaf ? 240 : 200,
      cellClass: isLeaf ? "" : "cursor-pointer font-medium text-indigo-600 dark:text-indigo-400",
    });
    const pct = (p: any) => (Number(p.value) || 0).toFixed(1);
    const gmvRatioCol = colNum("gmv_ratio", "GMV비중", "num", {
      minWidth: 92,
      valueFormatter: (p: any) => (p.value ?? 0).toFixed(1) + "%",
      cellStyle: (p: any) => {
        const a = Math.max(0, Math.min(1, (p.value || 0) / maxRatio));
        const [base, scale] = dark ? [0.14, 0.46] : [0.06, 0.5];
        const rgb = dark ? "129,140,248" : "99,102,241";
        return { textAlign: "right", backgroundColor: `rgba(${rgb},${(base + scale * a).toFixed(3)})`, ...(dark ? { color: "#e2e8f0" } : {}), fontWeight: a > 0.55 ? 600 : 400 };
      },
    });
    return [
      first,
      ...(isLeaf ? [colNum("key", "상품번호", "int", { minWidth: 96, valueFormatter: (p: any) => String(p.value ?? "") })] : []),
      colNum("qty", "순판매수량", "num", { minWidth: 96 }),
      colNum("gmv", "GMV", "num", { minWidth: 120 }),
      gmvRatioCol,   // GMV 바로 뒤 — 판매 탭과 동일 위치·형식
      colNum("normal_amt", "정상가매출", "num", { minWidth: 110 }),
      colNum("foreign_gmv", "외국인GMV", "num", { minWidth: 110 }),
      colNum("goods_count", "상품수", "int", { minWidth: 84 }),
      colNum("stock", "점재고", "num", { minWidth: 90 }),
      colNum("discount_rate", "할인율%", "num", { minWidth: 84, valueFormatter: pct }),
      colNum("foreign_ratio", "외국인비중%", "num", { minWidth: 96, valueFormatter: pct }),
    ];
  }, [level, isLeaf, maxRatio, dark]);

  return (
    <div className="space-y-4">
      {/* Breadcrumb */}
      <div className="flex flex-wrap items-center gap-1 text-sm">
        <button
          onClick={() => setPath([])}
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-slate-800"
        >
          <Home size={14} /> 전체
        </button>
        {path.map((p, i) => (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight size={14} className="text-slate-300 dark:text-slate-600" />
            <button
              onClick={() => setPath((prev) => prev.slice(0, i + 1))}
              className="rounded-md px-2 py-1 font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800"
            >
              {p.value}
            </button>
          </span>
        ))}
        <ChevronRight size={14} className="text-slate-300 dark:text-slate-600" />
        <span className="rounded-md bg-indigo-50 px-2 py-1 font-semibold text-indigo-700 ring-1 ring-inset ring-indigo-100 dark:bg-indigo-950/70 dark:text-indigo-300 dark:ring-indigo-900/60">
          {LABEL[level]}
        </span>
      </div>

      <Card><CardBody>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <ListTree size={16} className="text-indigo-500" />
            <div>
              <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">{LABEL[level]}별 판매 · 재고</h3>
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">
                {isLeaf ? "최하위(상품) 단계" : `${LABEL[level]} 클릭 → 하위 단계로 드릴다운`} · 총 {rows.length}행 · CSV는 tidy/long(행별 1건)
                {loading && " · 불러오는 중…"}
              </p>
            </div>
          </div>
          <button
            onClick={() => downloadDrillCsv(level, qs)}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 dark:hover:bg-indigo-500"
          >
            <Download size={14} /> CSV ({LABEL[level]}별)
          </button>
        </div>

        {error ? (
          <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between">
            <span>⚠ {error}</span>
            <button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button>
          </div>
        ) : !d ? (
          <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">불러오는 중…</span></div>
        ) : rows.length === 0 ? (
          <div className="p-10 text-center text-sm text-slate-400">조건에 해당하는 데이터가 없습니다.</div>
        ) : (
          <DataGrid rows={rows} columns={columns} dark={dark} height={560} onCellClicked={onCellClicked} />
        )}
      </CardBody></Card>
    </div>
  );
}
