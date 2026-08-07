import { useState } from "react";
import { LogIn, BarChart3, Loader2 } from "lucide-react";
import { api, setAuth } from "./lib";

export default function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    setLoading(true);
    try {
      const r = await api.login(u.trim(), p);
      setAuth(r.token, r.name);
      onDone();
    } catch (e: any) {
      setErr(e.message || "로그인 실패");
    } finally {
      setLoading(false);
    }
  }

  const inputCls =
    "w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none transition focus:border-indigo-500 focus-visible:ring-2 focus-visible:ring-indigo-500/30 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      {/* 장식용 브랜드 글로우 — 순수 시각 요소 */}
      <div aria-hidden className="pointer-events-none absolute -top-28 left-1/2 h-80 w-80 -translate-x-1/2 rounded-full bg-indigo-500/15 blur-3xl dark:bg-indigo-500/10" />
      <div className="animate-rise w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-indigo-600 text-white shadow-lg shadow-indigo-600/25">
            <BarChart3 size={24} />
          </div>
          <h1 className="text-lg font-bold tracking-tight text-slate-900 dark:text-slate-50">
            무신사 오프라인 대시보드
          </h1>
          <p className="mt-1 text-sm text-slate-400 dark:text-slate-400">로그인이 필요합니다</p>
        </div>

        <form
          onSubmit={submit}
          className="rounded-xl border border-slate-200 bg-white p-6 shadow-[var(--shadow-card)] dark:border-slate-800 dark:bg-slate-900"
        >
          <label className="mb-1 block text-sm font-medium text-slate-600 dark:text-slate-300">
            아이디
          </label>
          <input
            value={u}
            onChange={(e) => setU(e.target.value)}
            autoFocus
            className={"mb-4 " + inputCls}
            placeholder="minsu.kim"
          />
          <label className="mb-1 block text-sm font-medium text-slate-600 dark:text-slate-300">
            비밀번호
          </label>
          <input
            type="password"
            value={p}
            onChange={(e) => setP(e.target.value)}
            className={"mb-4 " + inputCls}
            placeholder="••••••••"
          />
          {err && (
            <div className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 dark:bg-rose-950/40 dark:text-rose-400">
              {err}
            </div>
          )}
          <button
            type="submit"
            disabled={loading || !u || !p}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 py-2.5 text-sm font-semibold text-white shadow-sm transition duration-150 hover:bg-indigo-500 hover:shadow-md active:scale-[0.99] disabled:pointer-events-none disabled:opacity-50"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />}
            로그인
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-slate-400 dark:text-slate-400">
          © MUSINSA · 사내 전용
        </p>
      </div>
    </div>
  );
}
