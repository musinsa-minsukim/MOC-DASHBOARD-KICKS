import { useEffect, useMemo, useState } from "react";
import { api, num } from "./lib";
import { Card, CardBody, Chip, Spinner, MultiSelect } from "./ui";
import DataGrid, { colText, colNum } from "./Grid";

type Meta = { shop_types: string[]; stores: { store_name: string; shop_type: string }[] };

export default function Pnl({ meta, dark }: { meta: Meta; dark: boolean }) {
  const [d, setD] = useState<any>(null);
  const [mode, setMode] = useState<"month" | "day">("month");
  const [level, setLevel] = useState<"store" | "brand">("store");
  const [period, setPeriod] = useState<string>("");
  const [types, setTypes] = useState<string[]>([]);
  const [stores, setStores] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const up = dark ? "#4ade80" : "#16a34a", down = dark ? "#f87171" : "#dc2626";

  const storeOpts = useMemo(() => {
    const pool = types.length ? meta.stores.filter((s) => types.includes(s.shop_type)) : meta.stores;
    return pool.map((s) => s.store_name);
  }, [meta.stores, types]);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    p.set("mode", mode); p.set("level", level);
    if (period) p.set("period", period);
    types.forEach((t) => p.append("type", t));
    stores.forEach((s) => p.append("store", s));
    return "?" + p.toString();
  }, [mode, level, period, types, stores]);

  useEffect(() => {
    let alive = true;
    setLoading(true); setError("");
    api.pnl(qs)
      .then((r) => { if (alive) { setD(r); if (!period && r?.period) setPeriod(r.period); } })
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [qs, reloadKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // 모드 전환 시 period 초기화(백엔드가 최신으로 기본값)
  const switchMode = (m: "month" | "day") => { if (m !== mode) { setMode(m); setPeriod(""); } };

  const pctCol = (field: string, header: string) =>
    colNum(field, header, "num", {
      minWidth: 96,
      valueFormatter: (p: any) => (p.value == null ? "—" : (p.value > 0 ? "+" : "") + (p.value as number).toFixed(1) + "%"),
      cellStyle: (p: any) => ({ textAlign: "right", fontWeight: 700, color: p.value == null ? "var(--ratio-neutral)" : (p.value as number) >= 0 ? up : down }),
    });
  const rateCol = (field: string, header: string) =>
    colNum(field, header, "num", { minWidth: 84, valueFormatter: (p: any) => (p.value == null ? "—" : (p.value as number).toFixed(1) + "%") });

  const pmLabel = mode === "day" ? "전일" : "전월";
  const pyLabel = mode === "day" ? "전년동일" : "전년동월";

  const cols = useMemo(() => {
    const c: any[] = [
      colText("name", level === "brand" ? "브랜드" : "매장", { pinned: "left", minWidth: 180 }),
    ];
    if (level === "store")
      c.push(colText("shop_type", "채널", { minWidth: 80, valueGetter: (p: any) => (p.data?.name === "합계" ? "" : p.data?.shop_type) }));
    c.push(
      colNum("gmv", "GMV(정산)", "compact", { minWidth: 104 }),
      colNum("net_take", "순매출(NetTake)", "compact", { minWidth: 116 }),
      rateCol("nt_rate", "순매출율"),
      colNum("cp", "공헌이익(CP)", "compact", { minWidth: 110 }),
      rateCol("cp_rate", "CP율"),
      colNum("offline_cost", "매장고정비", "compact", { minWidth: 104 }),
      colNum("pm_cp", `${pmLabel}CP`, "compact", { minWidth: 100 }),
      pctCol("pm_delta", `${pmLabel}대비`),
      colNum("py_cp", `${pyLabel}CP`, "compact", { minWidth: 108 }),
      pctCol("py_delta", `${pyLabel}대비`),
    );
    return c;
  }, [level, mode, dark]); // eslint-disable-line react-hooks/exhaustive-deps

  const total = useMemo(() => (d?.totals ? [d.totals] : []), [d]);
  const activeFilter = types.length + stores.length > 0;

  return (
    <div className="space-y-5">
      <Card><CardBody className="flex flex-wrap items-end gap-x-6 gap-y-3 p-4">
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">마감 기준</div>
          <div className="flex gap-1.5">
            <Chip active={mode === "month"} onClick={() => switchMode("month")}>월마감</Chip>
            <Chip active={mode === "day"} onClick={() => switchMode("day")}>일마감</Chip>
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">단위</div>
          <div className="flex gap-1.5">
            <Chip active={level === "store"} onClick={() => setLevel("store")}>매장별</Chip>
            <Chip active={level === "brand"} onClick={() => setLevel("brand")}>브랜드별</Chip>
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">{mode === "day" ? "일자" : "월"}</div>
          {mode === "day" ? (
            <input type="date" value={period || d?.period || ""} max={d?.max_date}
              onChange={(e) => setPeriod(e.target.value)}
              className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {(d?.months || []).slice(0, 14).map((m: string) => (
                <Chip key={m} active={(period || d?.period) === m} onClick={() => setPeriod(m)}>{m}</Chip>
              ))}
            </div>
          )}
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
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">매장{level === "brand" ? " (브랜드 범위)" : ""}</div>
          <MultiSelect label="매장" options={storeOpts} value={stores} onChange={setStores} />
        </div>
        {activeFilter && (
          <button onClick={() => { setTypes([]); setStores([]); }} className="self-end rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800">필터 초기화</button>
        )}
        {loading && <Spinner className="mb-1 h-4 w-4" />}
      </CardBody></Card>

      <p className="-mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-400 dark:text-slate-400">
        <span>{d?.period} · 공식 정산값(editorial) · Net Take=profit · CP=공헌이익 · GMV=정산기준(판매 탭 MOSS와 다름) · CP율 = CP ÷ (GMV/1.1)</span>
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
            <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">{level === "brand" ? "브랜드별" : "매장별"} 손익 · {d.period} {mode === "day" ? "(일마감)" : "(월마감)"}</h3>
            <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">CP 내림차순 · 합계 고정 · {pmLabel}·{pyLabel} CP 대비 · 열 정렬/이동</p>
          </div>
          <DataGrid rows={d.rows} columns={cols} dark={dark} pinnedTop={total}
            height={Math.min(860, 96 + ((d.rows?.length || 0) + 1) * 34)} />
        </CardBody></Card>
      )}
    </div>
  );
}
