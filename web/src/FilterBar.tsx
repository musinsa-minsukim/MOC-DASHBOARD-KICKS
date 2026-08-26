import { useMemo, useState } from "react";
import { RotateCcw, SlidersHorizontal, ChevronDown } from "lucide-react";
import { Card, CardBody, Chip, MultiSelect } from "./ui";
import { parseUids, daysBeforeISO, type Filters } from "./lib";

type Meta = {
  date_min: string;
  date_max: string;
  shop_types: string[];
  business_types: string[];
  stores: { store_name: string; shop_type: string }[];
  brands: string[];
  cat_top: string[];
  cat_large: string[];
  cat_medium: string[];
  md: string[];
};

export default function FilterBar({
  meta,
  f,
  setF,
  showGran = true,
  showDate = true,
}: {
  meta: Meta;
  f: Filters;
  setF: (f: Filters) => void;
  showGran?: boolean;
  showDate?: boolean;
}) {
  const storeOpts = useMemo(() => {
    const pool = f.type.length ? meta.stores.filter((s) => f.type.includes(s.shop_type)) : meta.stores;
    return pool.map((s) => s.store_name);
  }, [meta.stores, f.type]);
  const [open, setOpen] = useState(false); // 모바일 필터 접기/펼치기 (데스크탑 lg는 항상 펼침)

  const set = (patch: Partial<Filters>) => setF({ ...f, ...patch });
  // 기간 빠른 프리셋 (기준 = 최신 데이터일). 정확한 날짜는 입력칸 사용.
  const dmax = meta.date_max?.slice(0, 10) || "";
  const dmin = meta.date_min?.slice(0, 10) || "";
  const clampLo = (d: string) => (dmin && d < dmin ? dmin : d);
  const presets = (() => {
    if (!dmax) return [] as { label: string; from: string; to: string }[];
    const fmt = (dt: Date) => `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}-${String(dt.getDate()).padStart(2, "0")}`;
    const base = new Date(dmax + "T00:00:00");
    const dow = (base.getDay() + 6) % 7; // 월요일=0
    const monThis = new Date(base); monThis.setDate(base.getDate() - dow);           // 이번주 월
    const monLast = new Date(monThis); monLast.setDate(monThis.getDate() - 7);       // 지난주 월
    const sunLast = new Date(monThis); sunLast.setDate(monThis.getDate() - 1);       // 지난주 일
    const y = base.getFullYear(), m = base.getMonth();
    return [
      { label: "오늘", from: dmax, to: dmax },
      { label: "이번주", from: clampLo(fmt(monThis)), to: dmax },
      { label: "지난주", from: clampLo(fmt(monLast)), to: fmt(sunLast) },
      { label: "이번달", from: clampLo(fmt(new Date(y, m, 1))), to: dmax },
      { label: "지난달", from: clampLo(fmt(new Date(y, m - 1, 1))), to: fmt(new Date(y, m, 0)) },
      { label: "올해", from: clampLo(fmt(new Date(y, 0, 1))), to: dmax },
      { label: "전년", from: clampLo(fmt(new Date(y - 1, 0, 1))), to: fmt(new Date(y - 1, 11, 31)) },
    ];
  })();
  const uidCount = parseUids(f.goods).length;
  const nameOn = f.name_like.trim().length > 0;
  const active =
    f.biz.length || f.type.length || f.store.length || f.brand.length || f.brand_ex.length || f.cat_top.length ||
    f.cat_large.length || f.cat_medium.length || f.md.length || uidCount || nameOn;
  const activeN =
    f.biz.length + f.type.length + f.store.length + f.brand.length + f.brand_ex.length + f.cat_top.length +
    f.cat_large.length + f.cat_medium.length + f.md.length + (uidCount ? 1 : 0) + (nameOn ? 1 : 0);

  return (
    <Card>
      {/* 모바일: 얇은 필터 토글 — 고정바가 화면을 안 가리게. 데스크탑(lg)은 숨기고 항상 펼침 */}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-700 lg:hidden dark:text-slate-200"
      >
        <span className="flex items-center gap-1.5">
          <SlidersHorizontal size={15} /> 필터
          {activeN > 0 && (
            <span className="rounded-full bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white">{activeN}</span>
          )}
        </span>
        <ChevronDown size={16} className={`transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      <CardBody className={`${open ? "flex" : "hidden"} flex-wrap items-end gap-x-4 gap-y-3 p-4 lg:flex`}>
        {showDate && (
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">기간</div>
            <div className="flex items-center gap-2">
              <input type="date" value={f.date_from} min={meta.date_min?.slice(0, 10)} max={f.date_to || meta.date_max?.slice(0, 10)}
                onChange={(e) => set({ date_from: e.target.value, date_to: e.target.value > f.date_to ? e.target.value : f.date_to })}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:[color-scheme:dark]" />
              <span className="text-slate-300 dark:text-slate-500">~</span>
              <input type="date" value={f.date_to} min={f.date_from || meta.date_min?.slice(0, 10)} max={meta.date_max?.slice(0, 10)}
                onChange={(e) => set({ date_to: e.target.value, date_from: e.target.value < f.date_from ? e.target.value : f.date_from })}
                className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 dark:[color-scheme:dark]" />
            </div>
            {presets.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {presets.map((p) => (
                  <Chip key={p.label} active={f.date_from === p.from && f.date_to === p.to} onClick={() => set({ date_from: p.from, date_to: p.to })}>{p.label}</Chip>
                ))}
              </div>
            )}
          </div>
        )}

        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">사업구분</div>
          <div className="flex gap-1.5">
            {meta.business_types.filter((b) => b !== "기타").map((b) => (
              <Chip key={b} active={f.biz.includes(b)} onClick={() => set({ biz: f.biz.includes(b) ? f.biz.filter((x) => x !== b) : [...f.biz, b] })}>{b}</Chip>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">매장 타입</div>
          <div className="flex flex-wrap gap-1.5">
            {meta.shop_types.map((t) => (
              <Chip key={t} active={f.type.includes(t)} onClick={() => {
                const nt = f.type.includes(t) ? f.type.filter((x) => x !== t) : [...f.type, t];
                // 타입 변경 시 더 이상 후보가 아닌 매장 선택 해제
                const pool = nt.length ? meta.stores.filter((s) => nt.includes(s.shop_type)).map((s) => s.store_name) : meta.stores.map((s) => s.store_name);
                set({ type: nt, store: f.store.filter((s) => pool.includes(s)) });
              }}>{t}</Chip>
            ))}
          </div>
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">상세 필터</div>
          <div className="flex flex-wrap gap-1.5">
            <MultiSelect label="매장" options={storeOpts} value={f.store} onChange={(v) => set({ store: v })} />
            <MultiSelect label="브랜드" options={meta.brands} value={f.brand} onChange={(v) => set({ brand: v })} />
            <MultiSelect label="브랜드 제외" options={meta.brands} value={f.brand_ex} onChange={(v) => set({ brand_ex: v })} />
            <MultiSelect label="최상위" options={meta.cat_top} value={f.cat_top} onChange={(v) => set({ cat_top: v })} />
            <MultiSelect label="대카테" options={meta.cat_large} value={f.cat_large} onChange={(v) => set({ cat_large: v })} />
            <MultiSelect label="중카테" options={meta.cat_medium} value={f.cat_medium} onChange={(v) => set({ cat_medium: v })} />
            <MultiSelect label="MD" options={meta.md} value={f.md} onChange={(v) => set({ md: v })} />
          </div>
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">상품번호(UID){uidCount ? ` · ${uidCount}` : ""}</div>
          <input
            value={f.goods}
            onChange={(e) => set({ goods: e.target.value })}
            placeholder="5943430, 5943431 …"
            className="w-44 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
          />
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">상품명 포함</div>
          <div className="flex items-center gap-1.5">
            <Chip active={f.name_like.trim().toLowerCase() === "acg"}
              onClick={() => set({ name_like: f.name_like.trim().toLowerCase() === "acg" ? "" : "ACG" })}>ACG</Chip>
            <input
              value={f.name_like}
              onChange={(e) => set({ name_like: e.target.value })}
              placeholder="상품명 일부"
              className="w-28 rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm outline-none transition focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/25 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
            />
          </div>
        </div>

        {showGran && (
          <div>
            <div className="mb-1 text-xs font-medium text-slate-400 dark:text-slate-400">추이 단위</div>
            <div className="flex gap-1.5">
              {(["day", "week", "month"] as const).map((g) => (
                <Chip key={g} active={f.gran === g} onClick={() => set({ gran: g })}>{g === "day" ? "일" : g === "week" ? "주" : "월"}</Chip>
              ))}
            </div>
          </div>
        )}

        {active ? (
          <button
            onClick={() => set({ biz: [], type: [], store: [], brand: [], brand_ex: [], cat_top: [], cat_large: [], cat_medium: [], md: [], goods: "", name_like: "" })}
            className="ml-auto inline-flex items-center gap-1.5 self-end rounded-lg border border-slate-200 px-3 py-1.5 text-sm text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"
          >
            <RotateCcw size={13} /> 필터 초기화
          </button>
        ) : null}
      </CardBody>
    </Card>
  );
}
