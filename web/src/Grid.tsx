import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { ModuleRegistry, AllCommunityModule, themeQuartz } from "ag-grid-community";
import type { ColDef, CellStyle } from "ag-grid-community";
import { Pin, ChevronUp, ChevronDown } from "lucide-react";
import { won as wonFmt, num as numFmt, compact as compactFmt } from "./lib";

ModuleRegistry.registerModules([AllCommunityModule]);

// 커뮤니티 AG Grid는 헤더 메뉴 핀 옵션이 없음 → 헤더에 직접 핀 토글 버튼 + 정렬 표시.
function HeaderWithPin(props: any) {
  const { displayName, column, enableSorting, progressSort, api } = props;
  const [sort, setSort] = useState<string | null>(column.getSort?.() ?? null);
  const [pinned, setPinned] = useState<string | null>(column.getPinned?.() ?? null);
  useEffect(() => {
    const onSort = () => setSort(column.getSort?.() ?? null);
    const onPin = () => setPinned(column.getPinned?.() ?? null);
    column.addEventListener("sortChanged", onSort);
    api.addEventListener("columnPinned", onPin);
    return () => {
      column.removeEventListener("sortChanged", onSort);
      api.removeEventListener("columnPinned", onPin);
    };
  }, [column, api]);
  const togglePin = (e: any) => {
    e.stopPropagation();
    api.applyColumnState({ state: [{ colId: column.getColId(), pinned: pinned === "left" ? null : "left" }] });
  };
  return (
    <div
      className="flex w-full items-center gap-1"
      style={{ cursor: enableSorting ? "pointer" : "default" }}
      onClick={() => enableSorting && progressSort()}
    >
      <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{displayName}</span>
      {sort === "asc" && <ChevronUp size={13} />}
      {sort === "desc" && <ChevronDown size={13} />}
      <span
        onClick={togglePin}
        title={pinned === "left" ? "고정 해제" : "왼쪽 고정"}
        className="flex shrink-0 items-center"
        style={{ opacity: pinned === "left" ? 1 : 0.3 }}
      >
        <Pin size={12} />
      </span>
    </div>
  );
}

const common = {
  fontFamily: "inherit",
  fontSize: 13,
  headerFontSize: 12,
  headerFontWeight: 600,
  wrapperBorderRadius: 14,
  cellHorizontalPadding: 14,
  rowVerticalPaddingScale: 0.95,
  spacing: 7,
};
const lightTheme = themeQuartz.withParams({
  ...common, accentColor: "#4f46e5", backgroundColor: "#ffffff", foregroundColor: "#334155",
  borderColor: "#eef2f7", headerBackgroundColor: "#f8fafc", headerTextColor: "#64748b",
  rowHoverColor: "#f5f6fb", oddRowBackgroundColor: "#ffffff", wrapperBorder: "#e7ebf1",
});
const darkTheme = themeQuartz.withParams({
  ...common, accentColor: "#818cf8", backgroundColor: "#0f172a", foregroundColor: "#cbd5e1",
  borderColor: "#1e293b", headerBackgroundColor: "#111c31", headerTextColor: "#94a3b8",
  rowHoverColor: "#1b2740", oddRowBackgroundColor: "#0f172a", wrapperBorder: "#1e293b",
});

export type { ColDef };

type Fmt = "num" | "won" | "compact" | "int";
function fmtVal(v: any, fmt: Fmt): string {
  if (v === null || v === undefined || v === "") return "";
  if (fmt === "won") return wonFmt(v);
  if (fmt === "compact") return compactFmt(v);
  return numFmt(v); // num / int
}

// 셀 텍스트가 칸을 넘으면 "…"로 자르는 대신 글자 크기를 줄여 한 줄에 맞춤(엑셀 '셀에 맞춤'과 유사).
// 칸 너비(=셀 콘텐츠 폭)에 맞춰 base 13px를 비율 축소(최소 8px). 컬럼 리사이즈 시 ResizeObserver로 재계산.
function FitText(props: any) {
  const ref = useRef<HTMLSpanElement>(null);
  const text = props.valueFormatted ?? props.value ?? "";
  useLayoutEffect(() => {
    const el = ref.current; if (!el) return;
    const cell = el.parentElement; if (!cell) return;
    const fit = () => {
      el.style.fontSize = ""; // base(13px)로 리셋 후 자연 폭 측정
      const cs = getComputedStyle(cell);
      const avail = cell.clientWidth - parseFloat(cs.paddingLeft || "0") - parseFloat(cs.paddingRight || "0");
      const natural = el.scrollWidth;
      if (avail > 0 && natural > avail) {
        el.style.fontSize = `${Math.max(8, Math.min(13, (avail / natural) * 13))}px`;
      }
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(cell);
    return () => ro.disconnect();
  }, [text]);
  return <span ref={ref} style={{ display: "inline-block", whiteSpace: "nowrap" }} title={String(text)}>{String(text)}</span>;
}

// ---- 컬럼 정의 헬퍼 ----
// 텍스트 컬럼. 모바일에선 글자 축소(shrink-to-fit), PC에선 내용 길이에 맞춰 열 너비 자동조정 → DataGrid에서 분기.
export function colText(field: string, header: string, opts: Partial<ColDef> = {}): ColDef {
  return { field, headerName: header, minWidth: 110, ...opts };
}
export function colNum(field: string, header: string, fmt: Fmt = "num", opts: Partial<ColDef> = {}): ColDef {
  return {
    field, headerName: header, type: "numericColumn", minWidth: 96,
    valueFormatter: (p: any) => fmtVal(p.value, fmt), ...opts,
  };
}
// 신장율 등 색상 문자열 셀 (+초록 / -빨강 / 신규 파랑 / X·— 회색)
export function colRatio(field: string, header: string, opts: Partial<ColDef> = {}): ColDef {
  return {
    field, headerName: header, type: "numericColumn", minWidth: 84,
    cellStyle: (p: any): CellStyle => {
      const v = p.value;
      if (typeof v !== "string") return { textAlign: "right" };
      let color = "var(--ratio-neutral)";
      if (v.endsWith("%")) color = v.startsWith("-") ? "var(--ratio-down)" : "var(--ratio-up)";
      else if (v === "신규") color = "var(--ratio-new)";
      return { textAlign: "right", color, fontWeight: 600 };
    },
    ...opts,
  };
}

// 탭/줄바꿈은 공백으로 치환(엑셀 칸 깨짐 방지)
const cleanTSV = (s: string) => s.replace(/[\t\r\n]+/g, " ");

// 한 셀의 화면 표시 값(포맷 적용)을 문자열로 — 복사용.
// AG Grid v36: getCellValue({useFormatter:true})가 컬럼 valueFormatter를 적용한 표시값을 반환.
function fmtCell(api: any, col: any, node: any): string {
  let t: any;
  try { t = api.getCellValue({ rowNode: node, colKey: col, useFormatter: true }); } catch { t = null; }
  if (t === null || t === undefined) {
    try { t = node?.data?.[col.getColDef().field]; } catch { t = null; }
  }
  return t === null || t === undefined ? "" : cleanTSV(String(t));
}

// 컬럼의 헤더 표시명
function headerName(col: any): string {
  const d = col.getColDef();
  return cleanTSV(String(d.headerName ?? d.field ?? col.getColId() ?? ""));
}

type Cell = { row: number; ci: number }; // ci = 표시 컬럼 인덱스

export default function DataGrid({
  rows, columns, dark, height = 440, pinnedTop, onCellClicked, getRowClass,
}: {
  rows: any[];
  columns: ColDef[];
  dark: boolean;
  height?: number;
  pinnedTop?: any[];
  onCellClicked?: (e: any) => void;   // 크로스필터용(라벨 셀 클릭). 복사 선택과 별개로 동작.
  getRowClass?: (p: any) => string;   // 행 클래스(예: __muTotal 합계행 강조). 정렬 시에도 상단 고정됨.
}) {
  const apiRef = useRef<any>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  const anchorRef = useRef<Cell | null>(null); // 선택 고정 모서리
  const focusRef = useRef<Cell | null>(null);  // 선택 이동 모서리(활성 셀)
  const rangeRef = useRef<{ r0: number; r1: number; colIds: Set<string> } | null>(null);
  const keyRef = useRef<string>("");
  const tRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [copiedMsg, setCopiedMsg] = useState<string | null>(null);
  // 모바일(좁은 화면): 글자 축소 / PC: 열 너비를 내용에 맞춰 자동 확장
  const [isMobile, setIsMobile] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const h = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);

  // PC: 데이터가 바뀌면 각 열을 내용(헤더 포함) 길이에 맞춰 자동 너비 조정
  const autoSize = () => {
    if (isMobile) return;
    const api = apiRef.current; if (!api) return;
    try {
      api.autoSizeAllColumns(false);
      api.refreshCells({ force: true });   // pinned(합계) 행이 새 열 너비로 다시 그려지도록 → 본문과 정렬
    } catch { /* noop */ }
  };
  useEffect(() => {
    if (isMobile) return;
    const id = requestAnimationFrame(autoSize);
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, isMobile]);

  // pinnedTop(합계) 행 초기 반영. ⚠️ 이 그리드 설정에선 pinnedTopRowData의 셀 DOM이 값 변경 시
  // 리렌더되지 않는 한계가 있음(데이터모델만 갱신됨 — setGridOption/redrawRows/refreshCells 모두 무효).
  // → 합계가 '필터에 반영'돼야 하는 표는 호출부에서 데이터 바뀔 때 <DataGrid key=...>로 리마운트할 것
  //   (리마운트 시 초기 렌더로 pinned가 정확히 그려짐 = 고정 + 반영 동시 만족). 여기선 초기 세팅만.
  const pinnedKey = useMemo(() => JSON.stringify(pinnedTop ?? []), [pinnedTop]);
  useEffect(() => {
    const api = apiRef.current;
    if (!api) return;
    try { api.setGridOption("pinnedTopRowData", pinnedTop ?? []); api.refreshCells({ force: true }); } catch { /* noop */ }
  }, [pinnedKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const flash = (msg: string) => {
    setCopiedMsg(msg);
    if (tRef.current) clearTimeout(tRef.current);
    tRef.current = setTimeout(() => setCopiedMsg(null), 1200);
  };

  // 선택된 열의 헤더 하이라이트 동기화 (가로 스크롤로 헤더가 재생성돼도 유지)
  const paintHeaders = () => {
    const wrap = wrapRef.current; if (!wrap) return;
    const ids = rangeRef.current?.colIds;
    wrap.querySelectorAll(".ag-header-cell").forEach((h) => {
      const cid = h.getAttribute("col-id");
      (h as HTMLElement).classList.toggle("mu-hcol", !!cid && !!ids && ids.has(cid));
    });
  };

  // anchor~focus 사각형을 행 인덱스 + 표시 컬럼 인덱스로 계산해 하이라이트(복사 X)
  const applyRange = (a: Cell, f: Cell) => {
    const api = apiRef.current; if (!api) return;
    const cols = api.getAllDisplayedColumns(); if (!cols.length) return;
    const c0 = Math.max(0, Math.min(a.ci, f.ci)), c1 = Math.min(cols.length - 1, Math.max(a.ci, f.ci));
    const r0 = Math.min(a.row, f.row), r1 = Math.max(a.row, f.row);
    const key = `${r0}:${r1}:${c0}:${c1}`;
    if (key === keyRef.current) return; // 변동 없으면 리렌더 스킵
    keyRef.current = key;
    const colIds = new Set<string>(cols.slice(c0, c1 + 1).map((c: any) => String(c.getColId())));
    rangeRef.current = { r0, r1, colIds };
    api.refreshCells({ force: true });
    paintHeaders(); // 선택 열 헤더 하이라이트
  };

  // 선택 범위를 TSV(열=탭, 행=줄바꿈)로 → 엑셀/시트에 칸 맞춰 붙여넣기 (Ctrl/Cmd+C 전용)
  const copyRange = () => {
    const api = apiRef.current, rg = rangeRef.current; if (!api || !rg) return;
    const cols = api.getAllDisplayedColumns().filter((c: any) => rg.colIds.has(c.getColId()));
    const lines: string[] = [];
    for (let r = rg.r0; r <= rg.r1; r++) {
      const node = api.getDisplayedRowAtIndex(r);
      if (!node || node.rowPinned) continue;
      lines.push(cols.map((c: any) => fmtCell(api, c, node)).join("\t"));
    }
    if (!lines.length || !navigator.clipboard) return;
    const nr = lines.length, nc = cols.length;
    const multi = nr > 1 || nc > 1;
    // 여러 셀 선택이면 열 이름(헤더)을 첫 줄로 포함 → 엑셀에 제목까지 칸 맞춰 붙여넣기
    const out = (multi ? [cols.map((c: any) => headerName(c)).join("\t"), ...lines] : lines).join("\n");
    navigator.clipboard.writeText(out).then(
      () => flash(multi ? `${nr}행 × ${nc}열 · 헤더 포함 복사됨` : "복사됨"),
      () => {},
    );
  };

  const colIndex = (colId: string) =>
    apiRef.current ? apiRef.current.getAllDisplayedColumns().findIndex((c: any) => c.getColId() === colId) : -1;

  // 마우스: 클릭=단일 셀 선택, 드래그=사각 범위 선택 (복사는 하지 않음)
  const onCellMouseDown = (e: any) => {
    if (e?.node?.rowPinned || e.rowIndex == null) { draggingRef.current = false; return; }
    if (e.event && (e.event.button === 1 || e.event.button === 2)) return; // 가운데/오른쪽 무시
    const ci = colIndex(e.column.getColId()); if (ci < 0) return;
    anchorRef.current = { row: e.rowIndex, ci };
    focusRef.current = { row: e.rowIndex, ci };
    draggingRef.current = true;
    keyRef.current = "";
    applyRange(anchorRef.current, focusRef.current);
  };
  const onCellMouseOver = (e: any) => {
    if (!draggingRef.current || !anchorRef.current || e?.node?.rowPinned || e.rowIndex == null) return;
    const ci = colIndex(e.column.getColId()); if (ci < 0) return;
    focusRef.current = { row: e.rowIndex, ci };
    applyRange(anchorRef.current, focusRef.current);
  };

  // 키보드: 방향키 이동 / Shift+방향키 범위 확장 / Ctrl(⌘)+방향키 끝으로 / Ctrl(⌘)+C 복사.
  // capture 단계에서 처리해 AG Grid 기본 내비게이션과 중복 이동 방지.
  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const onKey = (ev: KeyboardEvent) => {
      const api = apiRef.current; if (!api) return;
      if ((ev.ctrlKey || ev.metaKey) && (ev.key === "c" || ev.key === "C")) {
        if (rangeRef.current) { ev.preventDefault(); ev.stopPropagation(); copyRange(); }
        return;
      }
      const dir =
        ev.key === "ArrowUp" ? [-1, 0] : ev.key === "ArrowDown" ? [1, 0] :
        ev.key === "ArrowLeft" ? [0, -1] : ev.key === "ArrowRight" ? [0, 1] : null;
      if (!dir) return;
      const cols = api.getAllDisplayedColumns();
      const nCols = cols.length, nRows = api.getDisplayedRowCount();
      if (!nCols || !nRows) return;
      ev.preventDefault(); ev.stopPropagation();
      let f = focusRef.current;
      if (!f) {
        const fc = api.getFocusedCell?.();
        const ci = fc ? cols.findIndex((c: any) => c.getColId() === fc.column.getColId()) : 0;
        f = { row: fc ? fc.rowIndex : 0, ci: ci < 0 ? 0 : ci };
      }
      let nr = f.row, nc = f.ci;
      if (ev.ctrlKey || ev.metaKey) { // 끝으로 점프 (엑셀 Ctrl+방향키)
        if (dir[0] < 0) nr = 0; else if (dir[0] > 0) nr = nRows - 1;
        if (dir[1] < 0) nc = 0; else if (dir[1] > 0) nc = nCols - 1;
      } else {
        nr = Math.min(nRows - 1, Math.max(0, nr + dir[0]));
        nc = Math.min(nCols - 1, Math.max(0, nc + dir[1]));
      }
      const focus = { row: nr, ci: nc };
      focusRef.current = focus;
      if (!ev.shiftKey) anchorRef.current = focus; // Shift 없으면 단일 셀로 접힘
      applyRange(anchorRef.current || focus, focus);
      const colId = cols[nc].getColId();
      api.ensureIndexVisible(nr);
      api.ensureColumnVisible(colId);
      api.setFocusedCell(nr, colId);
    };
    const up = () => { draggingRef.current = false; };
    el.addEventListener("keydown", onKey, true);
    window.addEventListener("mouseup", up);
    return () => {
      el.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mouseup", up);
      if (tRef.current) clearTimeout(tRef.current);
    };
  }, []);

  // 범위 하이라이트용 cellClassRules 주입 + 모바일이면 텍스트 열에 글자축소(FitText) 렌더러 주입
  const gridCols = useMemo(
    () => columns.map((c) => {
      const isText = (c as any).type !== "numericColumn" && !(c as any).cellRenderer;
      return {
        ...c,
        ...(isMobile && isText ? { cellRenderer: FitText } : {}),
        cellClassRules: {
          ...((c as any).cellClassRules || {}),
          "mu-range": (p: any) => {
            const rg = rangeRef.current;
            return !!rg && !p?.node?.rowPinned && p.rowIndex >= rg.r0 && p.rowIndex <= rg.r1 && rg.colIds.has(p.column.getColId());
          },
        },
      };
    }),
    [columns, isMobile],
  );

  return (
    <div ref={wrapRef} style={{ width: "100%", height, position: "relative", userSelect: "none" }}>
      <AgGridReact
        theme={dark ? darkTheme : lightTheme}
        rowData={rows}
        columnDefs={gridCols}
        pinnedTopRowData={pinnedTop}
        getRowClass={getRowClass}
        postSortRows={(p: any) => {
          // __muTotal(합계) 행은 정렬과 무관하게 항상 최상단 고정
          const rs = p.nodes;
          for (let i = 0; i < rs.length; i++) {
            if (rs[i]?.data?.__muTotal) { const [t] = rs.splice(i, 1); rs.unshift(t); break; }
          }
        }}
        defaultColDef={{
          sortable: true, resizable: true, suppressMovable: false, headerComponent: HeaderWithPin,
          // 방향키·Ctrl/Cmd+C는 우리가 직접 처리 → AG Grid 기본 동작 차단(이중 이동 방지)
          suppressKeyboardEvent: (p: any) => {
            const e = p.event; if (!e) return false;
            if (e.key === "ArrowUp" || e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "ArrowRight") return true;
            return (e.ctrlKey || e.metaKey) && (e.key === "c" || e.key === "C");
          },
        }}
        suppressDragLeaveHidesColumns
        suppressColumnVirtualisation
        animateRows={false}
        headerHeight={40}
        rowHeight={38}
        ensureDomOrder
        onGridReady={(e: any) => { apiRef.current = e.api; }}
        onFirstDataRendered={autoSize}
        onRowDataUpdated={autoSize}
        onCellClicked={onCellClicked}
        onCellMouseDown={onCellMouseDown}
        onCellMouseOver={onCellMouseOver}
        onBodyScroll={paintHeaders}
      />
      {copiedMsg && (
        <div className="animate-pop pointer-events-none absolute right-3 top-2 z-10 rounded-lg bg-slate-900/90 px-2.5 py-1 text-[11px] font-medium text-white shadow-[var(--shadow-pop)] dark:bg-slate-100/95 dark:text-slate-900">
          {copiedMsg}
        </div>
      )}
    </div>
  );
}
