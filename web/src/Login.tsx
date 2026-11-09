import { useEffect, useRef, useState } from "react";
import { LogIn, BarChart3, Loader2 } from "lucide-react";
import { api, setAuth } from "./lib";

type Cfg = { google_client_id?: string; google_hd?: string; password_login?: boolean };

declare global {
  interface Window {
    google?: any;
  }
}

export default function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [gLoading, setGLoading] = useState(false);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const btnRef = useRef<HTMLDivElement>(null);

  // 로그인 UI 설정(구글 클라이언트ID·비밀번호 로그인 가능 여부) 로드
  useEffect(() => {
    api.config().then(setCfg).catch(() => setCfg({ password_login: true }));
  }, []);

  async function handleGoogle(credential: string) {
    setErr("");
    setGLoading(true);
    try {
      const r = await api.googleLogin(credential);
      setAuth(r.token, r.name);
      onDone();
    } catch (e: any) {
      setErr(e.message || "구글 로그인 실패");
    } finally {
      setGLoading(false);
    }
  }

  // Google Identity Services 버튼 렌더 (클라이언트ID 있을 때만)
  useEffect(() => {
    const cid = cfg?.google_client_id;
    if (!cid) return;
    let cancelled = false;
    const init = () => {
      if (cancelled) return;
      const g = window.google;
      if (!g?.accounts?.id || !btnRef.current) return;
      g.accounts.id.initialize({ client_id: cid, callback: (resp: any) => handleGoogle(resp.credential) });
      btnRef.current.innerHTML = "";
      g.accounts.id.renderButton(btnRef.current, {
        theme: "outline", size: "large", width: 320, text: "signin_with", shape: "pill",
      });
    };
    if (window.google?.accounts?.id) {
      init();
      return;
    }
    let s = document.getElementById("gis-script") as HTMLScriptElement | null;
    if (!s) {
      s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.id = "gis-script";
      document.head.appendChild(s);
    }
    s.addEventListener("load", init);
    return () => {
      cancelled = true;
      s?.removeEventListener("load", init);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg?.google_client_id]);

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

  const showGoogle = !!cfg?.google_client_id;
  const showPassword = cfg !== null && cfg.password_login !== false;

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
          <p className="mt-1 text-sm text-slate-400 dark:text-slate-400">사내 구글 계정으로 로그인</p>
        </div>

        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-[var(--shadow-card)] dark:border-slate-800 dark:bg-slate-900">
          {cfg === null && (
            <div className="flex items-center justify-center py-6 text-slate-400">
              <Loader2 size={18} className="animate-spin" />
            </div>
          )}

          {showGoogle && (
            <div className="flex flex-col items-center">
              <div ref={btnRef} className="min-h-[40px]" />
              {gLoading && (
                <div className="mt-2 flex items-center gap-2 text-xs text-slate-400">
                  <Loader2 size={14} className="animate-spin" /> 확인 중…
                </div>
              )}
            </div>
          )}

          {showGoogle && showPassword && (
            <div className="my-4 flex items-center gap-3 text-xs text-slate-400">
              <div className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
              또는
              <div className="h-px flex-1 bg-slate-200 dark:bg-slate-700" />
            </div>
          )}

          {showPassword && (
            <form onSubmit={submit}>
              <label className="mb-1 block text-sm font-medium text-slate-600 dark:text-slate-300">아이디</label>
              <input value={u} onChange={(e) => setU(e.target.value)} className={"mb-4 " + inputCls} placeholder="minsu.kim" />
              <label className="mb-1 block text-sm font-medium text-slate-600 dark:text-slate-300">비밀번호</label>
              <input type="password" value={p} onChange={(e) => setP(e.target.value)} className={"mb-4 " + inputCls} placeholder="••••••••" />
              <button
                type="submit"
                disabled={loading || !u || !p}
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 py-2.5 text-sm font-semibold text-white shadow-sm transition duration-150 hover:bg-indigo-500 hover:shadow-md active:scale-[0.99] disabled:pointer-events-none disabled:opacity-50"
              >
                {loading ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />}
                로그인
              </button>
            </form>
          )}

          {err && (
            <div className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-600 dark:bg-rose-950/40 dark:text-rose-400">
              {err}
            </div>
          )}
        </div>
        <p className="mt-4 text-center text-xs text-slate-400 dark:text-slate-400">
          © MUSINSA · 사내 전용
        </p>
      </div>
    </div>
  );
}
