import { useEffect, useMemo, useState } from "react";
import { api } from "./lib";
import { Card, CardBody, SectionTitle, Chip, Spinner } from "./ui";
import { Save, Check } from "lucide-react";

// 층 지정 탭 — 월 × 매장(킥스 홍대/성수) 별로 재고 보유 브랜드의 층을 지정. 저장 시 GCS(floor_map)에 기록.
//   원천에 층이 없어 (월×매장×브랜드)→층 매핑을 여기서 운영 → 판매 탭 '층별 매출' 그래프에 반영.
export default function Floor({ dark }: { meta?: any; dark: boolean }) {
  const thisMonth = new Date().toISOString().slice(0, 7);
  const [month, setMonth] = useState<string>(thisMonth);
  const [store, setStore] = useState<string>("");
  const [payload, setPayload] = useState<any>(null);
  const [assign, setAssign] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string>("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setPayload(null);
    api.floor(month).then((p: any) => {
      if (!alive) return;
      setPayload(p);
      if (p.stores?.length && !p.stores.includes(store)) setStore(p.stores[0]);
    }).catch(() => alive && setPayload({ stores: [], brands: {}, map: {} }));
    return () => { alive = false; };
  }, [month, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  // 선택 월·매장의 유효값 로드(상속 반영: 과거월=기준월, 미래월=직전 명시월 이월)
  useEffect(() => {
    if (!payload || !store) return;
    setAssign({ ...((payload.effective?.[store]) ?? {}) });
  }, [payload, store, month]);

  const brands: string[] = payload?.brands?.[store] ?? [];
  const assignedCount = useMemo(() => Object.values(assign).filter((v) => v && String(v).trim()).length, [assign]);
  // 값 출처: source===month → 이 달 전용(저장/동결) / 다른 월 → 그 달 상속 중(미래월 라이브) / null → 기준월도 비어있음
  const srcMonth: string | null = payload?.source?.[store] ?? null;
  const baseMonth: string = payload?.base ?? "";
  const nowMonth: string = payload?.now ?? "";
  const inherited = !!srcMonth && srcMonth !== month;   // 미래월: 직전월 라이브 반영 중(아직 미동결)

  const setFloor = (brand: string, v: string) => setAssign((a) => ({ ...a, [brand]: v }));

  const save = async () => {
    setSaving(true); setMsg("");
    try {
      const clean: Record<string, string> = {};
      for (const [b, f] of Object.entries(assign)) if (f && String(f).trim()) clean[b] = String(f).trim();
      await api.floorSave({ month, store, assignments: clean });
      setMsg("저장되었습니다");
      setReload((k) => k + 1);
      setTimeout(() => setMsg(""), 2500);
    } catch (e: any) {
      setMsg("저장 실패: " + (e?.message ?? ""));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">층 지정</h1>
          <p className="mt-0.5 text-sm text-slate-400 dark:text-slate-400">킥스 홍대·성수의 <b>월별</b> 브랜드→층 매핑 · 판매 탭 ‘층별 매출’에 반영 · 원천에 층이 없어 여기서 운영</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input type="month" value={month} onChange={(e) => setMonth(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
          {(payload?.stores ?? []).map((s: string) => (
            <Chip key={s} active={store === s} onClick={() => setStore(s)}>{s.replace("무신사 ", "")}</Chip>
          ))}
        </div>
      </div>

      {!payload ? (
        <div className="flex h-60 items-center justify-center"><Spinner className="h-7 w-7" /></div>
      ) : (
        <Card><CardBody>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <SectionTitle title={`${store.replace("무신사 ", "")} · ${month} 브랜드 층 지정`} sub={`재고 보유 브랜드 ${brands.length}개 · 지정 ${assignedCount}개 · 자유 입력(예: 1F / 2F / B1)`} />
              <div className="mt-1 text-xs">
                {inherited ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-amber-50 px-2 py-0.5 font-medium text-amber-700 dark:bg-amber-500/10 dark:text-amber-400">
                    {month > nowMonth ? "직전월" : "기준월"}({srcMonth}) 설정을 반영 중(미래월 · 미동결) · 저장하면 {month} 전용으로 고정됩니다
                  </span>
                ) : srcMonth === month ? (
                  <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400">
                    {month === baseMonth ? "이 달 설정 (기준월)" : "이 달 전용 설정 (고정됨 · 기준월 수정에 영향 없음)"}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 font-medium text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    상속할 기준월({baseMonth}) 설정이 비어 있습니다
                  </span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {msg && <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400"><Check size={13} />{msg}</span>}
              <button onClick={save} disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
                <Save size={14} />{saving ? "저장 중…" : "저장"}
              </button>
            </div>
          </div>
          {brands.length === 0 ? (
            <div className="p-8 text-center text-sm text-slate-400 dark:text-slate-400">재고 보유 브랜드가 없습니다 (재고 캐시 갱신 후 표시).</div>
          ) : (
            <div className="grid grid-cols-1 gap-x-8 gap-y-1 md:grid-cols-2 xl:grid-cols-3">
              {brands.map((b) => (
                <div key={b} className="flex items-center gap-2 border-b border-slate-50 py-1.5 dark:border-slate-800/50">
                  <span className="min-w-0 flex-1 truncate text-sm text-slate-700 dark:text-slate-200" title={b}>{b}</span>
                  <input value={assign[b] ?? ""} onChange={(e) => setFloor(b, e.target.value)} placeholder="층"
                    className="w-20 rounded-md border border-slate-200 bg-white px-2 py-1 text-right text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200" />
                </div>
              ))}
            </div>
          )}
        </CardBody></Card>
      )}
    </div>
  );
}
