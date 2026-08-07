import { useEffect, useMemo, useState } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LabelList,
} from "recharts";
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Store,
  Tags,
  Package,
  ShoppingBag,
  AlertTriangle,
  Flame,
  Download,
  RotateCw,
  CircleAlert,
  CircleCheck,
} from "lucide-react";
import { api, getToken, won, num, compact } from "./lib";
import { Card, CardBody, SectionTitle, Spinner, Chip, FitText } from "./ui";
import { HolidayTick, isRedDay } from "./holidays";
import { trendLabel } from "./chartlabels";

type Daily = any;

function DeltaBadge({ v }: { v: number | null }) {
  if (v === null || v === undefined)
    return <span className="text-xs text-slate-400 dark:text-slate-400">—</span>;
  const up = v >= 0;
  const Icon = v === 0 ? Minus : up ? TrendingUp : TrendingDown;
  const cls =
    v === 0
      ? "text-slate-500 bg-slate-100 dark:bg-slate-800 dark:text-slate-400"
      : up
        ? "text-emerald-700 bg-emerald-50 dark:bg-emerald-950/50 dark:text-emerald-400"
        : "text-rose-700 bg-rose-50 dark:bg-rose-950/50 dark:text-rose-400";
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-semibold ${cls}`}>
      <Icon size={12} />
      {(up && v !== 0 ? "+" : "") + v.toFixed(1)}%
    </span>
  );
}

function Kpi({ icon: Icon, label, value, sub, delta }: { icon: any; label: string; value: string; sub?: string; delta?: number | null }) {
  return (
    <Card>
      <CardBody className="p-4 md:p-5">
        <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400">
          <Icon size={15} className="shrink-0" />
          <span className="line-clamp-2 min-w-0 text-xs font-medium leading-tight" title={label}>{label}</span>
        </div>
        <div className="mt-2 truncate text-xl font-bold tabular-nums tracking-tight text-slate-900 md:text-2xl dark:text-slate-50" title={value}><FitText>{value}</FitText></div>
        <div className="mt-1 flex items-center gap-2">
          {delta !== undefined && <DeltaBadge v={delta} />}
          {sub && <span className="truncate text-xs text-slate-400 dark:text-slate-400">{sub}</span>}
        </div>
      </CardBody>
    </Card>
  );
}

function BarRow({ rank, name, meta, value, share, color }: { rank: number; name: string; meta?: string; value: string; share: number; color: string }) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="w-6 shrink-0 text-right text-xs font-semibold text-slate-300 dark:text-slate-500">{rank}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-medium text-slate-700 dark:text-slate-200" title={name}>{name}</span>
          <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-800 dark:text-slate-100">{value}</span>
        </div>
        <div className="mt-1 flex items-center gap-2">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
            <div className="h-full rounded-full" style={{ width: `${Math.max(2, Math.min(100, share))}%`, backgroundColor: color }} />
          </div>
          <span className="w-14 shrink-0 text-right text-[11px] text-slate-400 dark:text-slate-400">{meta}</span>
        </div>
      </div>
    </div>
  );
}

const NOTABLE_TAG: Record<string, { cls: string; icon: any; label: string }> = {
  판매TOP: { cls: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400", icon: Flame, label: "판매TOP" },
  급등: { cls: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400", icon: TrendingUp, label: "급등" },
  급락: { cls: "bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400", icon: TrendingDown, label: "급락" },
};

// 재고보충 전체 CSV: 인증 헤더가 필요하므로 fetch→blob 다운로드 (백엔드가 전체 행 생성)
async function downloadRestockCsv(qs: string, latest: string) {
  const r = await fetch("/api/daily/restock.csv" + qs, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!r.ok) {
    alert("CSV 다운로드 실패 (" + r.status + ")");
    return;
  }
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `restock_${latest}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Summary({ dark }: { meta?: any; dark: boolean }) {
  const [seg, setSeg] = useState<string>("전체");
  const [d, setD] = useState<Daily | null>(null);
  const [err, setErr] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const qs = useMemo(() => {
    const p = new URLSearchParams();
    if (seg && seg !== "전체") p.append("seg", seg);
    return p.toString() ? "?" + p.toString() : "";
  }, [seg]);

  useEffect(() => {
    let alive = true;
    setD(null);
    setErr("");
    api
      .daily(qs)
      .then((r) => alive && setD(r))
      .catch((e) => alive && setErr(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qs, reloadKey]);

  const C = dark
    ? { grid: "#1e293b", axis: "#94a3b8", ttFg: "#cbd5e1", ttBg: "#0f172a", ttBorder: "#334155", area: "#818cf8" }
    : { grid: "#f1f5f9", axis: "#94a3b8", ttFg: "#475569", ttBg: "#ffffff", ttBorder: "#e2e8f0", area: "#4f46e5" };

  if (err)
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 p-6 text-center dark:border-rose-900 dark:bg-rose-950/30">
        <p className="text-sm text-rose-600 dark:text-rose-400">데이터를 불러오지 못했습니다: {err}</p>
        <button onClick={() => setReloadKey((k) => k + 1)} className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-700">
          <RotateCw size={14} /> 다시 시도
        </button>
      </div>
    );

  return (
    <div className="space-y-5">
      {/* 헤더 + 기준 선택 */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">오프라인 MD 일별 매출 리포트</h1>
          <p className="mt-0.5 text-sm text-slate-400 dark:text-slate-400">
            {d ? <>최신 데이터일 <span className="font-semibold text-slate-600 dark:text-slate-300">{d.latest}</span>{d.prev && <> · 직전일({d.prev}) 대비 · 사이드바 필터와 무관</>}</> : "불러오는 중…"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {["전체", "매입", "위탁"].map((s) => (
            <Chip key={s} active={seg === s} onClick={() => setSeg(s)}>{s}</Chip>
          ))}
        </div>
      </div>

      {!d ? (
        <div className="flex h-72 flex-col items-center justify-center gap-3 text-slate-400 dark:text-slate-400">
          <Spinner className="h-7 w-7" />
          <span className="text-sm">리포트 불러오는 중…</span>
        </div>
      ) : d.empty ? (
        <div className="p-10 text-center text-sm text-slate-400">선택한 기준에 해당하는 판매 데이터가 없습니다.</div>
      ) : (
        <Report d={d} C={C} qs={qs} />
      )}
    </div>
  );
}

function Report({ d, C, qs }: { d: Daily; C: any; qs: string }) {
  const t = d.totals;
  const a = d.actions;
  const maxStore = Math.max(...d.stores.map((s: any) => s.gmv), 1);
  const maxBrand = Math.max(...d.brands.map((b: any) => b.gmv), 1);
  const dnames: string[] = d.dnames;

  return (
    <div className="space-y-5">
      {/* 1) 종합 분석 */}
      <Card>
        <CardBody>
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">종합 분석</h3>
            {d.issue ? (
              <span className="inline-flex items-center gap-1 rounded-md bg-rose-50 px-2 py-1 text-xs font-semibold text-rose-700 dark:bg-rose-950/50 dark:text-rose-400">
                <CircleAlert size={13} /> 이슈
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-400">
                <CircleCheck size={13} /> 양호
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi icon={ShoppingBag} label="전일 GMV" value={won(t.gmv)} delta={t.gmv_delta} />
            <Kpi icon={Package} label="전일 판매수량" value={num(t.qty) + "개"} sub={`외국인 ${t.foreign_ratio.toFixed(1)}%`} />
            <Kpi icon={Store} label={`선두 매장 · ${d.lead_store?.name ?? "-"}`} value={`${(d.lead_store?.share ?? 0).toFixed(1)}%`} sub={compact(d.lead_store?.gmv ?? 0)} />
            <Kpi icon={Tags} label={`주도 브랜드 · ${d.lead_brand?.name ?? "-"}`} value={`${(d.lead_brand?.share ?? 0).toFixed(1)}%`} sub={compact(d.lead_brand?.gmv ?? 0)} />
          </div>
          <ul className="mt-4 space-y-1 text-sm text-slate-600 dark:text-slate-300">
            <li>· 전일 GMV <b className="text-slate-800 dark:text-slate-100">{won(t.gmv)}</b> ({num(t.qty)}개 판매){t.gmv_delta !== null && <>, 직전일 대비 <b className={t.gmv_delta >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}>{(t.gmv_delta >= 0 ? "+" : "") + t.gmv_delta.toFixed(1)}%</b></>}</li>
            <li>· 재고보충 필요 <b className="text-slate-800 dark:text-slate-100">{num(a.restock_count)}건</b> · 긴급(점재고 0) <b className="text-rose-600 dark:text-rose-400">{num(a.urgent_count)}건</b> — 오늘 중 허브 발주 검토</li>
          </ul>
        </CardBody>
      </Card>

      {/* 중카테고리별 브랜드 랭킹 (#6) */}
      {Array.isArray(d.cat_brand) && d.cat_brand.length > 0 && (
        <Card>
          <CardBody>
            <SectionTitle title="중카테고리별 브랜드 랭킹" sub={`${d.basis} · 전일(${d.latest}) 기준 · GMV 상위 브랜드 · 비중·전일 대비`} />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {d.cat_brand.map((c: any) => (
                <div key={c.cat} className="rounded-lg border border-slate-100 p-3 dark:border-slate-800">
                  <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 pb-1.5 dark:border-slate-800">
                    <span className="truncate font-semibold text-slate-800 dark:text-slate-100" title={c.cat}>{c.cat}</span>
                    <span className="shrink-0 text-xs text-slate-500 dark:text-slate-400">합계 {won(c.total)}</span>
                  </div>
                  <ol className="mt-1.5 space-y-1">
                    {c.brands.map((b: any, i: number) => (
                      <li key={b.name + i} className="flex items-center gap-2 text-sm">
                        <span className="w-4 shrink-0 text-right text-xs font-semibold text-slate-300 dark:text-slate-500">{i + 1}</span>
                        <span className="min-w-0 flex-1 truncate text-slate-700 dark:text-slate-200" title={b.name}>{b.name}</span>
                        <span className="shrink-0 tabular-nums text-slate-600 dark:text-slate-300">{compact(b.gmv)}</span>
                        <span className="w-9 shrink-0 text-right text-xs text-slate-400 dark:text-slate-400">{b.share.toFixed(0)}%</span>
                        <span className="w-14 shrink-0 text-right"><DeltaBadge v={b.delta} /></span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* 중카테고리별 상품 랭킹 TOP10 (#1) */}
      {Array.isArray(d.cat_goods) && d.cat_goods.length > 0 && (
        <Card>
          <CardBody>
            <SectionTitle title="중카테고리별 상품 랭킹 TOP 10" sub={`${d.basis} · 전일(${d.latest}) 기준 · GMV 상위 상품 · 비중·전일 대비`} />
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {d.cat_goods.map((c: any) => (
                <div key={c.cat} className="rounded-lg border border-slate-100 p-3 dark:border-slate-800">
                  <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 pb-1.5 dark:border-slate-800">
                    <span className="truncate font-semibold text-slate-800 dark:text-slate-100" title={c.cat}>{c.cat}</span>
                    <span className="shrink-0 text-xs text-slate-500 dark:text-slate-400">합계 {won(c.total)}</span>
                  </div>
                  <ol className="mt-1.5 space-y-1">
                    {c.goods.map((g: any, i: number) => (
                      <li key={g.goods_no} className="flex items-center gap-2 text-sm">
                        <span className="w-4 shrink-0 text-right text-xs font-semibold text-slate-300 dark:text-slate-500">{i + 1}</span>
                        <span className="min-w-0 flex-1 truncate" title={`${g.brand} · ${g.name}`}>
                          <span className="text-slate-700 dark:text-slate-200">{g.name}</span>
                          <span className="ml-1 text-[11px] text-slate-400 dark:text-slate-400">{g.brand}</span>
                        </span>
                        <span className="shrink-0 tabular-nums text-slate-600 dark:text-slate-300">{compact(g.gmv)}</span>
                        <span className="w-9 shrink-0 text-right text-xs text-slate-400 dark:text-slate-400">{g.share.toFixed(0)}%</span>
                        <span className="w-14 shrink-0 text-right"><DeltaBadge v={g.delta} /></span>
                      </li>
                    ))}
                  </ol>
                </div>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* 2) 액션 포인트 */}
      <Card>
        <CardBody>
          <SectionTitle title="오늘 유의할 액션 포인트" />
          <ul className="space-y-2 text-sm">
            {a.spike_up && (
              <li className="flex items-start gap-2 text-slate-600 dark:text-slate-300">
                <TrendingUp size={16} className="mt-0.5 shrink-0 text-emerald-500" />
                <span><b className="text-slate-800 dark:text-slate-100">{a.spike_up.store}</b> 전일 대비 판매수량 <b className="text-emerald-600 dark:text-emerald-400">{(a.spike_up.pct >= 0 ? "+" : "") + a.spike_up.pct.toFixed(0)}%</b> 급등 — 진열·재고 점검 권장</span>
              </li>
            )}
            {a.spike_down && (
              <li className="flex items-start gap-2 text-slate-600 dark:text-slate-300">
                <TrendingDown size={16} className="mt-0.5 shrink-0 text-rose-500" />
                <span><b className="text-slate-800 dark:text-slate-100">{a.spike_down.store}</b> 전일 대비 판매수량 <b className="text-rose-600 dark:text-rose-400">{(a.spike_down.pct >= 0 ? "+" : "") + a.spike_down.pct.toFixed(0)}%</b> 급락 — 원인 파악 필요</span>
              </li>
            )}
            {a.urgent_first && (
              <li className="flex items-start gap-2 text-slate-600 dark:text-slate-300">
                <AlertTriangle size={16} className="mt-0.5 shrink-0 text-amber-500" />
                <span>긴급보충 <b className="text-rose-600 dark:text-rose-400">{a.urgent_count}건</b> (점재고 0 + 전일 판매) — 1순위 [<b className="text-slate-800 dark:text-slate-100">{a.urgent_first.store}</b>] {a.urgent_first.brand} {a.urgent_first.goods_nm} (전일 {num(a.urgent_first.sold)}개 판매)</span>
              </li>
            )}
            {!a.spike_up && !a.spike_down && !a.urgent_first && (
              <li className="text-slate-400 dark:text-slate-400">특이 액션 없음</li>
            )}
          </ul>
        </CardBody>
      </Card>

      {/* 3) 주목 상품 추이 */}
      <Card>
        <CardBody>
          <SectionTitle title="주목 상품 추이" sub="🔥 판매TOP · 📈 급등 · 📉 급락 · 최근 4일 수량 + 재고" />
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-left text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
                  <th className="py-2 pr-2 font-medium">태그</th>
                  <th className="py-2 pr-3 font-medium">브랜드</th>
                  <th className="py-2 pr-3 font-medium">UID</th>
                  <th className="py-2 pr-3 font-medium">상품</th>
                  {dnames.map((n) => <th key={n} className="py-2 pr-3 text-right font-medium">{n}</th>)}
                  <th className="py-2 pr-3 text-right font-medium">점재고</th>
                  <th className="py-2 pr-3 text-right font-medium">허브1000</th>
                  <th className="py-2 pr-3 text-right font-medium">허브1700</th>
                  <th className="py-2 text-right font-medium">MFS</th>
                </tr>
              </thead>
              <tbody>
                {d.notable.length === 0 && (
                  <tr><td colSpan={8 + dnames.length} className="py-6 text-center text-slate-400">데이터 없음</td></tr>
                )}
                {d.notable.map((n: any) => {
                  const ts = NOTABLE_TAG[n.tag] || NOTABLE_TAG["판매TOP"];
                  return (
                    <tr key={n.goods_no} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                      <td className="py-2 pr-2 whitespace-nowrap">
                        <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ${ts.cls}`}>
                          <ts.icon size={11} />{ts.label}{n.tag_pct !== null ? ` ${n.tag_pct >= 0 ? "+" : ""}${n.tag_pct.toFixed(0)}%` : ""}
                        </span>
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap text-slate-500 dark:text-slate-400">{n.brand}</td>
                      <td className="py-2 pr-3 whitespace-nowrap tabular-nums text-slate-400 dark:text-slate-400">{n.goods_no}</td>
                      <td className="max-w-[220px] truncate py-2 pr-3 text-slate-700 dark:text-slate-200" title={n.name}><FitText>{n.name}</FitText></td>
                      {n.days.map((q: number, i: number) => (
                        <td key={i} className={`py-2 pr-3 text-right tabular-nums ${i === 0 ? "font-semibold text-slate-800 dark:text-slate-100" : "text-slate-500 dark:text-slate-400"}`}>{num(q)}</td>
                      ))}
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-500 dark:text-slate-400">{num(n.store_stock)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-400 dark:text-slate-400">{num(n.hub1000)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-slate-400 dark:text-slate-400">{num(n.hub1700)}</td>
                      <td className="py-2 text-right tabular-nums text-slate-400 dark:text-slate-400">{num(n.mfs)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      {/* 4) 최근 14일 추이 + 전일~-4일 */}
      <Card>
        <CardBody>
          <SectionTitle title="전일 전체 실적" sub="최근 14일 추이 · 전일~-4일" />
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={d.trend} margin={{ top: 5, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="gA" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.area} stopOpacity={0.35} />
                  <stop offset="100%" stopColor={C.area} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke={C.grid} vertical={false} />
              <XAxis dataKey="date" tick={<HolidayTick axisColor={C.axis} day={true} format={(v: string) => String(v).slice(5)} />} axisLine={false} tickLine={false} />
              <YAxis tickFormatter={(v) => compact(v)} tick={{ fontSize: 11, fill: C.axis }} axisLine={false} tickLine={false} width={48} />
              <Tooltip formatter={(v: any) => [won(v as number), "매출"]} contentStyle={{ background: C.ttBg, border: `1px solid ${C.ttBorder}`, borderRadius: 10, fontSize: 12, color: C.ttFg }} labelStyle={{ color: C.ttFg }} />
              <Area type="monotone" dataKey="gmv" stroke={C.area} strokeWidth={2} fill="url(#gA)">
                <LabelList position="top" content={trendLabel(d.trend.length, compact, C.ttFg)} />
              </Area>
            </AreaChart>
          </ResponsiveContainer>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {d.trend4.map((x: any) => (
              <div key={x.label} className="rounded-lg border border-slate-100 p-2.5 dark:border-slate-800">
                <div className={"text-[11px] " + (isRedDay(x.date) ? "font-semibold text-red-500" : "text-slate-400 dark:text-slate-400")}>{x.label} · {String(x.date).slice(5)}</div>
                <div className="mt-0.5 font-semibold text-slate-800 dark:text-slate-100">{compact(x.gmv)}</div>
                <div className="text-xs text-slate-400 dark:text-slate-400">{num(x.qty)}개</div>
              </div>
            ))}
          </div>
        </CardBody>
      </Card>

      {/* 매장별 */}
      <Card>
        <CardBody>
          <SectionTitle title="매장별 실적 (전일)" sub={`${d.stores.length}개 매장`} />
          {d.stores.map((s: any, i: number) => (
            <BarRow key={s.name} rank={i + 1} name={s.name} meta={s.share.toFixed(1) + "%"} value={compact(s.gmv)} share={(s.gmv / maxStore) * 100} color={C.area} />
          ))}
        </CardBody>
      </Card>

      {/* 브랜드 / 상품 TOP100 */}
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardBody>
            <SectionTitle title="브랜드별 실적 TOP 100" sub={`${d.brands.length}개`} />
            <div className="max-h-[420px] overflow-y-auto pr-1">
              {d.brands.map((b: any, i: number) => (
                <BarRow key={b.name} rank={i + 1} name={b.name} meta={b.share.toFixed(1) + "%"} value={compact(b.gmv)} share={(b.gmv / maxBrand) * 100} color="#8b5cf6" />
              ))}
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <SectionTitle title="상품별 실적 TOP 100" sub={`${d.goods.length}개`} />
            <div className="max-h-[420px] overflow-x-auto overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white dark:bg-slate-900">
                  <tr className="border-b border-slate-100 text-left text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
                    <th className="py-2 pr-2 font-medium">#</th>
                    <th className="py-2 pr-3 font-medium">브랜드</th>
                    <th className="py-2 pr-3 font-medium">상품</th>
                    <th className="py-2 pr-3 text-right font-medium">GMV</th>
                    <th className="py-2 text-right font-medium">수량</th>
                  </tr>
                </thead>
                <tbody>
                  {d.goods.map((g: any, i: number) => (
                    <tr key={g.goods_no} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                      <td className="py-2 pr-2 text-xs text-slate-300 dark:text-slate-500">{i + 1}</td>
                      <td className="py-2 pr-3 whitespace-nowrap text-slate-500 dark:text-slate-400">{g.brand}</td>
                      <td className="max-w-[200px] truncate py-2 pr-3 text-slate-700 dark:text-slate-200" title={g.name}><FitText>{g.name}</FitText></td>
                      <td className="py-2 pr-3 text-right font-semibold tabular-nums text-slate-800 dark:text-slate-100">{compact(g.gmv)}</td>
                      <td className="py-2 text-right tabular-nums text-slate-500 dark:text-slate-400">{num(g.qty)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* 재고보충 + CSV */}
      <Card>
        <CardBody>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-[15px] font-semibold text-slate-800 dark:text-slate-100">재고보충 필요 상품</h3>
              <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-400">전체 {num(a.restock_count)}건 · 기준: 허브재고 보유 + 전일판매&gt;0 + 점재고&lt;전일판매×2</p>
            </div>
            <button
              onClick={() => downloadRestockCsv(qs, d.latest)}
              disabled={!d.restock.length}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              <Download size={14} /> CSV 다운로드
            </button>
          </div>
          {a.restock_count > d.restock.length && (
            <p className="mb-2 text-xs text-amber-600 dark:text-amber-400">※ 표시·CSV는 상위 {num(d.restock.length)}건까지 (전체 {num(a.restock_count)}건)</p>
          )}
          <div className="max-h-[460px] overflow-x-auto overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-white dark:bg-slate-900">
                <tr className="border-b border-slate-100 text-left text-xs text-slate-400 dark:border-slate-800 dark:text-slate-500">
                  <th className="py-2 pr-2 font-medium"></th>
                  <th className="py-2 pr-3 font-medium">매장</th>
                  <th className="py-2 pr-3 font-medium">브랜드</th>
                  <th className="py-2 pr-3 font-medium">상품</th>
                  <th className="py-2 pr-3 text-right font-medium">전일판매</th>
                  <th className="py-2 pr-3 text-right font-medium">점재고</th>
                  <th className="py-2 text-right font-medium">허브재고</th>
                </tr>
              </thead>
              <tbody>
                {d.restock.length === 0 && (
                  <tr><td colSpan={7} className="py-6 text-center text-slate-400">보충 필요 상품 없음</td></tr>
                )}
                {d.restock.slice(0, 200).map((x: any, i: number) => (
                  <tr key={i} className="border-b border-slate-50 last:border-0 dark:border-slate-800/60">
                    <td className="py-2 pr-2">
                      <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${x.urgent ? "bg-rose-50 text-rose-700 dark:bg-rose-950/50 dark:text-rose-400" : "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400"}`}>
                        {x.urgent ? "긴급" : "보충"}
                      </span>
                    </td>
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-600 dark:text-slate-300">{x.store}</td>
                    <td className="py-2 pr-3 whitespace-nowrap text-slate-500 dark:text-slate-400">{x.brand}</td>
                    <td className="max-w-[200px] truncate py-2 pr-3 text-slate-700 dark:text-slate-200" title={x.name}><FitText>{x.name}</FitText></td>
                    <td className="py-2 pr-3 text-right font-semibold tabular-nums text-slate-800 dark:text-slate-100">{num(x.sold)}</td>
                    <td className="py-2 pr-3 text-right tabular-nums text-slate-500 dark:text-slate-400">{num(x.store_stock)}</td>
                    <td className="py-2 text-right tabular-nums text-emerald-600 dark:text-emerald-400">{num(x.hub_total)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.restock.length > 200 && (
            <p className="mt-2 text-xs text-slate-400 dark:text-slate-400">화면에는 상위 200건 표시 · 전체는 CSV로 받으세요</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
