import { useEffect, useMemo, useState } from "react";
import { api } from "./lib";
import { Card, CardBody, Chip, Spinner } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";

type Meta = { shop_types: string[]; stores: { store_name: string; shop_type: string }[] };

// 판매 탭과 동일한 비중 히트맵: 앵커(전체 비중) 대비 높으면 초록 → 낮으면 노랑·주황·빨강
function heatRateCol(field: string, header: string, rows: any[], anchor: number, dark: boolean) {
  const vals = rows.filter((r) => r[field] != null).map((r) => r[field] as number);
  const up = Math.max(1e-6, (vals.length ? Math.max(...vals) : anchor + 1) - anchor);
  const dn = Math.max(1e-6, anchor - (vals.length ? Math.min(...vals) : anchor - 1));
  return colNum(field, header, "num", {
    minWidth: 84,
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

export default function Pnl({ meta, dark }: { meta: Meta; dark: boolean }) {
  const [d, setD] = useState<any>(null);
  const [mode, setMode] = useState<"month" | "day" | "range">("month");
  const [period, setPeriod] = useState<string>("");
  const [rangeFrom, setRangeFrom] = useState<string>(""); // 기간 모드 from(YYYY-MM-DD)
  const [rangeTo, setRangeTo] = useState<string>("");     // 기간 모드 to
  const [types, setTypes] = useState<string[]>([]);
  const [drill, setDrill] = useState<string | null>(null); // null=매장별, 값=그 매장의 브랜드별
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const up = dark ? "#4ade80" : "#16a34a", down = dark ? "#f87171" : "#dc2626";
  const level = drill ? "brand" : "store";

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    p.set("mode", mode); p.set("level", level);
    if (mode === "range") {
      if (rangeFrom) p.set("date_from", rangeFrom);
      if (rangeTo) p.set("date_to", rangeTo);
    } else if (period) {
      p.set("period", period);
    }
    if (drill) p.set("store", drill);
    else types.forEach((t) => p.append("type", t));
    return "?" + p.toString();
  }, [mode, level, period, rangeFrom, rangeTo, types, drill]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.pnl(qs)
      .then((r) => {
        if (!alive) return;
        setD(r);
        if (mode !== "range" && !period && r?.period) setPeriod(r.period);
        // 기간 모드 최초 진입 시 서버가 정한 기본 범위(최신월 1일~max)를 입력에 채움
        if (mode === "range" && r?.range) { if (!rangeFrom) setRangeFrom(r.range.from); if (!rangeTo) setRangeTo(r.range.to); }
      })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const switchMode = (m: "month" | "day" | "range") => { if (m !== mode) { setMode(m); setPeriod(""); } };

  const pctCol = (field: string, header: string) =>
    colNum(field, header, "num", {
      minWidth: 96,
      valueFormatter: (p: any) => (p.value == null ? "—" : (p.value > 0 ? "+" : "") + (p.value as number).toFixed(1) + "%"),
      cellStyle: (p: any) => ({ textAlign: "right", fontWeight: 700, color: p.value == null ? "var(--ratio-neutral)" : (p.value as number) >= 0 ? up : down }),
    });

  const pmLabel = mode === "day" ? "전일" : mode === "range" ? "직전기간" : "전월";
  const pyLabel = mode === "day" ? "전년동일" : mode === "range" ? "전년동기간" : "전년동월";

  const cols = useMemo(() => {
    const rows = d?.rows || [];
    const T = d?.totals || {};
    const c: any[] = [
      colText("name", level === "brand" ? "브랜드" : "매장", {
        pinned: "left", minWidth: 180,
        cellStyle: (p: any): any => (level === "store" && p.data?.name && p.data?.name !== "합계" ? { cursor: "pointer", color: dark ? "#a5b4fc" : "#4f46e5", fontWeight: 600 } : {}),
      }),
    ];
    if (level === "store")
      c.push(colText("shop_type", "채널", { minWidth: 80 }));
    c.push(
      colNum("gmv", "GMV(정산)", "compact", { minWidth: 104 }),
      colNum("net_take", "순매출(NetTake)", "compact", { minWidth: 116 }),
      heatRateCol("nt_rate", "순매출율", rows, T.nt_rate ?? 0, dark),
      colNum("cp", "공헌이익(CP)", "compact", { minWidth: 110 }),
      heatRateCol("cp_rate", "CP율", rows, T.cp_rate ?? 0, dark),
      colNum("offline_cost", "매장고정비", "compact", { minWidth: 104 }),
      colNum("pm_cp", `${pmLabel}CP`, "compact", { minWidth: 100 }),
      pctCol("pm_delta", `${pmLabel}대비`),
      colNum("py_cp", `${pyLabel}CP`, "compact", { minWidth: 108 }),
      pctCol("py_delta", `${pyLabel}대비`),
    );
    return c;
  }, [d, level, mode, dark]); // eslint-disable-line react-hooks/exhaustive-deps

  // 합계행 — pinnedTop(상단 고정). pinnedTop은 값 변경 시 셀 리렌더가 안 되는 한계가 있어,
  // 데이터가 바뀌면(gridKey 변경) 그리드를 리마운트해 '고정 + 필터 반영'을 동시에 만족시킨다.
  const total = useMemo(() => (d?.totals ? [d.totals] : []), [d]);
  const gridKey = useMemo(() => `${d?.period}|${level}|${(d?.rows?.length) || 0}|${Math.round((d?.totals?.gmv) || 0)}|${Math.round((d?.totals?.cp) || 0)}`, [d, level]);

  const onCellClicked = (e: any) => {
    if (level !== "store" || e?.node?.rowPinned) return;
    const nm = e?.data?.name;
    if (nm && nm !== "합계") setDrill(nm);
  };

  return (
    <div className="space-y-5">
      <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">마감 기준</div>
          <div className="flex gap-1.5">
            <Chip active={mode === "month"} onClick={() => switchMode("month")}>월마감</Chip>
            <Chip active={mode === "day"} onClick={() => switchMode("day")}>일마감</Chip>
            <Chip active={mode === "range"} onClick={() => switchMode("range")}>기간</Chip>
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">
            {mode === "day" ? "일자" : mode === "range" ? "기간(from ~ to)" : "월"}
          </div>
          {mode === "day" ? (
            <input type="date" value={period || d?.period || ""} max={d?.max_date}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
          ) : mode === "range" ? (
            <div className="flex items-center gap-1.5">
              <input type="date" value={rangeFrom} min={d?.min_date} max={rangeTo || d?.max_date}
                onChange={(e) => setRangeFrom(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
              <span className="text-slate-400">~</span>
              <input type="date" value={rangeTo} min={rangeFrom || d?.min_date} max={d?.max_date}
                onChange={(e) => setRangeTo(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {(d?.months || []).slice(0, 14).map((m: string) => (
                <Chip key={m} active={(period || d?.period) === m} onClick={() => setPeriod(m)}>{m}</Chip>
              ))}
            </div>
          )}
        </div>
        {!drill && (
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">채널</div>
            <div className="flex flex-wrap gap-1.5">
              {meta.shop_types.map((t) => (
                <Chip key={t} active={types.includes(t)} onClick={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])}>{t}</Chip>
              ))}
            </div>
          </div>
        )}
        {loading && <Spinner className="mb-1 h-4 w-4" />}
      </CardBody></Card>

      {/* 드릴다운 브레드크럼 */}
      <div className="-mt-2 flex items-center gap-2 text-sm">
        <button onClick={() => setDrill(null)} className={`rounded-md px-2 py-0.5 font-medium ${drill ? "text-indigo-600 hover:bg-indigo-50 dark:text-indigo-300 dark:hover:bg-indigo-950/40" : "text-slate-500 dark:text-slate-300"}`}>전체 매장</button>
        {drill && <><span className="text-slate-300 dark:text-slate-600">›</span><span className="font-semibold text-slate-700 dark:text-slate-200">{drill}</span><span className="text-xs text-slate-400">(브랜드별)</span></>}
        {!drill && <span className="text-xs text-slate-400 dark:text-slate-500">· 매장 행 클릭 → 브랜드 드릴다운</span>}
      </div>

      <p className="-mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400 dark:text-slate-400">
        <span>{d?.period} · 정산 기준일 {d?.max_date || "—"} · 공식 정산값(editorial) · Net Take=profit · CP=공헌이익 · GMV=정산기준(판매 탭 MOSS와 다름) · CP율 = CP ÷ (GMV/1.1)</span>
        {d?.provisional && <span className="rounded-md bg-amber-100 px-2 py-0.5 font-semibold text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">최근 ~2개월 CP·고정비 잠정(SAP 확정 전)</span>}
      </p>

      {error && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-600 dark:border-rose-900/50 dark:bg-rose-950/40 dark:text-rose-400 flex items-center justify-between"><span>⚠ {error}</span><button onClick={() => setReloadKey((k) => k + 1)} className="rounded-md bg-rose-100 px-3 py-1 font-medium text-rose-700 dark:bg-rose-900/50 dark:text-rose-300">다시 시도</button></div>}

      {!d ? (
        <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400 dark:text-slate-400"><Spinner className="h-7 w-7" /><span className="text-sm">손익 데이터 불러오는 중…</span></div>
      ) : !d.available ? (
        <div className="p-10 text-center text-sm text-slate-400 dark:text-slate-400">손익 데이터(settlement_daily)가 아직 캐시에 없습니다. 다음 전체 갱신(mode=full) 후 표시됩니다.</div>
      ) : (
        <Card><CardBody>
          <div className="mb-3">
            <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">{drill ? `${drill} · 브랜드별` : "매장별"} 손익 · {d.period} {mode === "day" ? "(일마감)" : mode === "range" ? "(기간)" : "(월마감)"}</h3>
            <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">CP 내림차순 · 합계 고정 · {pmLabel}·{pyLabel} CP 대비 · 순매출율/CP율 = 전체 대비 색상</p>
          </div>
          <DataGrid key={gridKey} rows={d.rows} columns={cols} dark={dark} pinnedTop={total} onCellClicked={onCellClicked}
            height={Math.min(860, 96 + ((d.rows?.length || 0) + 1) * 34)} />
        </CardBody></Card>
      )}
    </div>
  );
}
