"""무신사 오프라인 매장 대시보드 — 판매(외국인 포함) / 고객·외국인 / 재고.

데이터는 세션당 1회 로딩(캐시), 모든 필터/집계/신장율은 pandas에서 즉시 처리.
디자인: 미니멀 SaaS — 인디고 액센트, 카드, 여백, KPI 미니차트.
"""
from __future__ import annotations

import os
import datetime as dt

import pandas as pd
import plotly.express as px
import streamlit as st

import db
import store

st.set_page_config(page_title="무신사 오프라인 대시보드", page_icon="📊", layout="wide")

# ============================ 로그인 게이트 ============================
# DASH_NO_AUTH=1 이면 우회(AppTest 검증용). 운영에서는 항상 로그인 필요.
if os.environ.get("DASH_NO_AUTH") != "1":
    import yaml
    import streamlit_authenticator as stauth

    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_config.yaml"), encoding="utf-8") as _f:
            _ac = yaml.safe_load(_f) or {}
    except FileNotFoundError:
        _ac = {}
    _users = (_ac.get("credentials") or {}).get("usernames") or {}
    if not _users:
        st.title("🔒 로그인")
        st.warning("등록된 계정이 없습니다. 터미널에서 `uv run python manage_users.py add <아이디>` 로 계정을 먼저 추가하세요.")
        st.stop()
    _ck = _ac.get("cookie", {})
    _authr = stauth.Authenticate(_ac["credentials"], _ck.get("name", "musinsa_dash_auth"),
                                 _ck.get("key", "change-me"), _ck.get("expiry_days", 30), auto_hash=False)
    _authr.login(location="main", fields={"Form name": "무신사 오프라인 대시보드", "Username": "아이디",
                                           "Password": "비밀번호", "Login": "로그인"})
    _status = st.session_state.get("authentication_status")
    if _status is False:
        st.error("아이디 또는 비밀번호가 올바르지 않습니다.")
        st.stop()
    if _status is None:
        st.info("아이디와 비밀번호를 입력해 로그인하세요.")
        st.stop()
    with st.sidebar:
        st.caption(f"👤 {st.session_state.get('name', '')} 로그인됨")
        _authr.logout("로그아웃", location="sidebar")

PRIMARY, VIOLET, FGN, SLATE = "#4f46e5", "#7c3aed", "#6366f1", "#94a3b8"
FGN = "#94b8e8"          # 외국인 — 차분한 소프트 블루
BIZ_COLORS = {"위탁": PRIMARY, "매입": VIOLET, "기타": SLATE}
SEX_COLORS = {"여성": "#ec4899", "남성": "#3b82f6", "기타": SLATE}
AGE_ORDER = ["초등학생", "중학생", "고등학생", "대학생", "20대초반", "20대후반", "30대초반", "30대후반",
             "40대초반", "40대후반", "50대초반", "50대후반", "60대이상", "기타"]
PLOT = dict(template="plotly_white")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], button, input { font-family: 'Inter','Pretendard',sans-serif; }
.block-container { padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1480px; }
/* 카드 (st.container(border=True)) */
div[data-testid="stVerticalBlockBorderWrapper"] {
  background:#fff; border:1px solid #e2e8f0 !important; border-radius:16px;
  box-shadow:0 1px 2px rgba(15,23,42,.04); padding:.4rem .25rem;
}
/* KPI */
div[data-testid="stMetric"] { padding:.2rem .4rem; }
div[data-testid="stMetricLabel"] p { color:#64748b; font-size:.82rem; font-weight:500; }
div[data-testid="stMetricValue"] { font-size:1.55rem; font-weight:700; color:#0f172a; }
/* 탭 */
button[data-baseweb="tab"] { font-weight:600; font-size:.95rem; }
div[data-baseweb="tab-highlight"], div[data-baseweb="tab-border"] { background-color:#4f46e5; }
/* 제목 */
h1 { font-weight:700; letter-spacing:-.02em; }
hr { margin:.6rem 0; }
[data-testid="stSidebar"] { background:#ffffff; border-right:1px solid #e2e8f0; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ---------------- 포맷 ----------------
def won(v):
    return f"{float(v or 0):,.0f}원"


def num(v):
    return f"{float(v or 0):,.0f}"


def delta_pct(cur, prev):
    return f"{(cur-prev)/abs(prev)*100:+.1f}%" if prev else None


def _rgba(hex_, a):
    h = hex_.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"


def section(title, sub=None):
    st.markdown(
        f"<div style='font-weight:600;font-size:1.02rem;color:#0f172a'>{title}</div>"
        + (f"<div style='color:#64748b;font-size:.82rem;margin:.1rem 0 .4rem'>{sub}</div>" if sub else "<div style='height:.4rem'></div>"),
        unsafe_allow_html=True)


def spark(d, x, y, color=PRIMARY):
    fig = px.area(d, x=x, y=y)
    fig.update_traces(line_color=color, line_width=2, fillcolor=_rgba(color, .12),
                      hovertemplate="%{x|%m-%d}<br>%{y:,.0f}<extra></extra>")
    fig.update_layout(template="plotly_white", height=64, margin=dict(l=0, r=0, t=2, b=0),
                      xaxis=dict(visible=False), yaxis=dict(visible=False), showlegend=False)
    return fig


def hbar(d, x, y, color=PRIMARY, height=430):
    d = d.copy()
    d["label"] = d[x].map(num)
    fig = px.bar(d, x=x, y=y, orientation="h", text="label", labels={x: "", y: ""})
    fig.update_traces(marker_color=color, textposition="outside", cliponaxis=False,
                      hovertemplate="%{y}<br>%{x:,.0f}<extra></extra>")
    fig.update_layout(**PLOT, height=height, margin=dict(l=0, r=14, t=6, b=0))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(title="")
    return fig


SEG_COLORS = {"내국인": PRIMARY, "외국인": FGN}


def _segment(df_, group, topn=None):
    g = df_.groupby(group, as_index=False).agg(gmv=("gmv", "sum"), f=("foreign_gmv", "sum"))
    if topn:
        g = g.sort_values("gmv", ascending=False).head(topn)
    g = g.sort_values("gmv")
    g["내국인"] = (g["gmv"] - g["f"]).clip(lower=0)
    g["외국인"] = g["f"].clip(lower=0)
    long = g.melt(id_vars=[group, "gmv"], value_vars=["내국인", "외국인"], var_name="구분", value_name="seg")
    long["ratio"] = (long["seg"] / long["gmv"].where(long["gmv"] != 0) * 100).fillna(0)
    return g, long


def stack_h(df_, group, topn=None, height=430):
    g, long = _segment(df_, group, topn)
    fig = px.bar(long, x="seg", y=group, color="구분", orientation="h", barmode="stack",
                 color_discrete_map=SEG_COLORS, category_orders={group: g[group].tolist()},
                 custom_data=["ratio"], labels={"seg": "", group: "", "구분": ""})
    fig.update_traces(hovertemplate="%{y} · %{fullData.name}<br>%{x:,.0f}원 (%{customdata[0]:.1f}%)<extra></extra>")
    fig.update_layout(**PLOT, height=height, margin=dict(l=0, r=60, t=6, b=0), legend_title_text="")
    for _, r in g.iterrows():
        fig.add_annotation(x=r["gmv"], y=r[group], text=num(r["gmv"]), showarrow=False,
                           xanchor="left", xshift=5, font=dict(size=11, color="#334155"))
    fig.update_xaxes(visible=False, range=[0, g["gmv"].max() * 1.18])
    return fig


def stack_v(df_, group, height=430):
    g, long = _segment(df_, group)
    fig = px.bar(long, x=group, y="seg", color="구분", barmode="stack",
                 color_discrete_map=SEG_COLORS, custom_data=["ratio"],
                 labels={"seg": "GMV(원)", group: "", "구분": ""})
    fig.update_traces(hovertemplate="%{x} · %{fullData.name}<br>%{y:,.0f}원 (%{customdata[0]:.1f}%)<extra></extra>")
    fig.update_layout(**PLOT, height=height, margin=dict(l=0, r=0, t=20, b=0), legend_title_text="")
    for _, r in g.iterrows():
        fig.add_annotation(x=r[group], y=r["gmv"], text=num(r["gmv"]), showarrow=False,
                           yanchor="bottom", yshift=4, font=dict(size=12, color="#334155"))
    fig.update_yaxes(range=[0, g["gmv"].max() * 1.15])
    return fig


def area(d, x, y, color=None, cmap=None, height=320):
    fig = px.area(d, x=x, y=y, color=color, color_discrete_map=cmap, labels={x: "", y: "", color or "": ""})
    fig.update_traces(hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f}<extra></extra>")
    fig.update_layout(**PLOT, height=height, margin=dict(l=0, r=0, t=6, b=0), legend_title_text="")
    return fig


_PC = {"n": 0}


def pchart(fig, width="stretch", config=None):
    _PC["n"] += 1
    getattr(st, "plotly_chart")(fig, width=width, key=f"pc_{_PC['n']}",
                                config=config or {"displayModeBar": False})


def daily(d, col):
    return d.groupby(d["sales_date"].dt.normalize())[col].sum().reset_index()


def add_total(df, label_col, skip=(), agg=None):
    """표 맨 위에 전체 합계 행을 prepend. 숫자 컬럼은 합산, 비율(할인율/외국인비중/비중)은 전체 기준 재계산.
    agg를 주면 합계는 agg(전체) 기준으로 계산하되 표시 행은 df(상위 N)만 사용."""
    if df.empty:
        return df
    base = agg if agg is not None else df
    t = {c: float("nan") for c in df.columns}
    t[label_col] = "합계"
    rate = ("할인율", "외국인비중")  # 비율(rate)은 합산하지 않고 전체 기준 재계산
    for c in df.columns:
        if c == label_col or c in skip or c in rate:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            # 비중(share)은 표시 행 기준 합(=표시분이 전체에서 차지하는 %), 그 외는 base 합
            src = df if c == "비중" else base
            t[c] = src[c].sum() if c in src.columns else df[c].sum()

    def _s(c):
        return pd.to_numeric(base[c], errors="coerce").sum() if c in base.columns else 0
    if "할인율" in df.columns and {"gmv", "normal_amt"} <= set(base.columns):
        d = _s("normal_amt")
        t["할인율"] = (1 - _s("gmv") / d) * 100 if d else 0
    if "외국인비중" in df.columns and {"gmv", "foreign_gmv"} <= set(base.columns):
        g = _s("gmv")
        t["외국인비중"] = (_s("foreign_gmv") / g * 100) if g else 0
    return pd.concat([pd.DataFrame([t]), df], ignore_index=True)


def bucketize(dt_series, unit):
    if unit == "주":
        return dt_series.dt.to_period("W").dt.start_time
    if unit == "월":
        return dt_series.dt.to_period("M").dt.start_time
    return dt_series.dt.normalize()


def kpi(col, label, value, delta, sdf=None, ycol=None, color=PRIMARY):
    with col:
        st.metric(label, value, delta)
        if sdf is not None and len(sdf) > 1:
            pchart(spark(sdf, "sales_date", ycol, color), width="stretch",
                            config={"displayModeBar": False})


# ============================ 데이터 + 필터 ============================
# 데이터 소스 = 로컬 DuckDB(store). 판매=증분 누적, 재고/상품/고객=스냅샷. 없으면 최초 1회 빌드.
_miss = store.missing()
if _miss:
    with st.spinner(f"최초 데이터 빌드 중 ({', '.join(_miss)}) — 수 분 소요, 1회만. 이후 즉시 로딩됩니다."):
        for _name in _miss:
            store.refresh_sales(full=True) if _name == "sales" else store.refresh_snapshot(_name)


@st.cache_data(show_spinner=False)
def _sales():
    return store.get_sales()


@st.cache_data(show_spinner=False)
def _goods_master():
    return store.get_goods_master()


@st.cache_data(show_spinner=False)
def _customer():
    return store.get_customer()


@st.cache_data(show_spinner=False)
def _inv_pivot():
    return store.get_inventory_pivot()


@st.cache_data(show_spinner=False)
def _inv_goods():
    return store.get_inventory_goods()


@st.cache_data(show_spinner=False)
def _inv_store_long():
    return store.get_inventory_store_long()


sales = _sales()
dmin, dmax = sales["sales_date"].min().date(), sales["sales_date"].max().date()
store_df = sales[["store_name", "shop_type"]].drop_duplicates().sort_values(["shop_type", "store_name"])
all_brands = sales.groupby("brand_nm")["gmv"].sum().sort_values(ascending=False).index.tolist()
cat_top_opts = sorted(sales["cat_top"].dropna().unique())
cat_large_opts = sorted(sales["cat_large"].dropna().unique())
cat_medium_opts = sorted(sales["cat_medium"].dropna().unique())

sales["offline_md"] = sales["off_md_id"].replace("", "(미지정)")
_cm = db.load_concept_map()
sales["concept"] = [_cm.get((str(c), str(b)), "미지정") for c, b in zip(sales["company_id"], sales["brand_id"])]
md_opts = sorted(sales.loc[sales["off_md_id"] != "", "offline_md"].unique())
concept_opts = sorted(sales.loc[sales["concept"] != "미지정", "concept"].unique())

st.title("무신사 오프라인 매장 대시보드")

with st.sidebar:
    st.markdown("### 필터")
    default_from = max(dmin, dmax - dt.timedelta(days=30))
    dr = st.date_input("기간", value=(default_from, dmax), min_value=dmin, max_value=dmax)
    date_from, date_to = dr if isinstance(dr, (tuple, list)) and len(dr) == 2 else (default_from, dmax)
    f_biz = st.multiselect("사업구분", ["위탁", "매입"], default=["위탁", "매입"])
    f_type = st.multiselect("매장 타입", sorted(store_df["shop_type"].unique()))
    pool = store_df[store_df["shop_type"].isin(f_type)] if f_type else store_df
    f_store = st.multiselect("매장", pool["store_name"].tolist())
    f_brand = st.multiselect("브랜드 (GMV 상위순)", all_brands)
    f_cat_top = st.multiselect("최상위 카테", cat_top_opts)
    f_cat_large = st.multiselect("대카테", cat_large_opts)
    f_cat_medium = st.multiselect("중카테", cat_medium_opts)
    f_md = st.multiselect("오프라인 MD", md_opts)
    f_concept = st.multiselect("컨셉값", concept_opts)
    f_goods_raw = st.text_area("상품번호(UID) 다중 입력", placeholder="예: 5943430, 5943431 (쉼표/공백/줄바꿈 구분)", height=72)
    f_goods = {int(t) for t in f_goods_raw.replace(",", " ").replace("\n", " ").split() if t.strip().isdigit()}
    if f_goods:
        st.caption(f"상품번호 {len(f_goods)}개 적용 중")
    gran = st.radio("추이 단위", ["일", "주", "월"], index=0, horizontal=True)
    st.divider()
    st.caption(f"데이터 기간 · {dmin} ~ {dmax}")
    _stt = store.status()
    st.caption(f"판매 갱신 {_stt.get('sales_refreshed_at') or '—'} · 재고/상품 {_stt.get('inventory_pivot_refreshed_at') or '—'}")
    _rc1, _rc2 = st.columns(2)
    if _rc1.button("판매 갱신", help="MOSS 주문/환불 증분 반영(빠름, ~40초)", use_container_width=True):
        with st.spinner("판매 증분 갱신 중…"):
            store.refresh_sales()
            _sales.clear()
        st.rerun()
    if _rc2.button("전체 갱신", help="판매+재고+상품 전체 최신화(수 분)", use_container_width=True):
        with st.spinner("전체 갱신 중… (수 분)"):
            st.cache_data.clear()        # db.load_*(원천 재조회) + 래퍼 캐시 비움
            store.refresh_sales()
            store.refresh_snapshots()
        st.rerun()

period_len = (date_to - date_from).days + 1
prev_to = date_from - dt.timedelta(days=1)
prev_from = prev_to - dt.timedelta(days=period_len - 1)


def sales_mask(df_, store_only=False):
    """날짜를 제외한 모든 사이드바 필터를 적용한 boolean mask."""
    m = pd.Series(True, index=df_.index)
    if f_type:
        m &= df_["shop_type"].isin(f_type)
    if f_store:
        m &= df_["store_name"].isin(f_store)
    if not store_only:
        if f_biz and "business_type" in df_:
            m &= df_["business_type"].isin(f_biz)
        if f_brand and "brand_nm" in df_:
            m &= df_["brand_nm"].isin(f_brand)
        for col, sel in (("cat_top", f_cat_top), ("cat_large", f_cat_large), ("cat_medium", f_cat_medium)):
            if sel and col in df_.columns:
                m &= df_[col].isin(sel)
        if f_md and "offline_md" in df_.columns:
            m &= df_["offline_md"].isin(f_md)
        if f_concept and "concept" in df_.columns:
            m &= df_["concept"].isin(f_concept)
        if f_goods and "goods_no" in df_.columns:
            m &= df_["goods_no"].isin(f_goods)
    return m


def date_split(df_, store_only=False):
    m = sales_mask(df_, store_only)
    d = df_["sales_date"].dt.date
    return df_[m & d.between(date_from, date_to)], df_[m & d.between(prev_from, prev_to)]


tab_sum, tab_sales, tab_cust, tab_cmp, tab_inv = st.tabs(["요약", "판매", "고객·외국인", "비교·신장율", "재고"])

# ============================ 요약 탭 (전일 일별 리포트) ============================
with tab_sum:
    bc1, bc2 = st.columns([1.2, 1])
    basis_opts = ["전체"] + sorted([c for c in sales["cat_top"].unique() if c and c != "미분류"])
    _didx = basis_opts.index("Shoes") if "Shoes" in basis_opts else 0
    basis = bc1.selectbox("최상위 카테 기준", basis_opts, index=_didx, key="sum_basis")
    seg = bc2.radio("사업구분", ["전체", "매입", "위탁"], horizontal=True, key="sum_seg")
    s_all = sales if basis == "전체" else sales[sales["cat_top"] == basis]
    if seg != "전체":
        s_all = s_all[s_all["business_type"] == seg]
    inv_s = _inv_pivot()
    _mh = {"barcode", "goods_no", "goods_opt", "brand_nm", "goods_nm", "business_type",
           "cat_top", "cat_large", "cat_medium", "점재고합계", "허브합계", *db.HUB_COLS}
    inv_store_cols = [c for c in inv_s.columns if c not in _mh]

    day = s_all["sales_date"].dt.normalize()
    dts = sorted(day.unique())
    if not dts:
        st.info("데이터가 없습니다.")
    else:
        dlist = list(dts[-4:])[::-1]          # [전일, -2, -3, -4]
        dnames = ["전일", "-2일", "-3일", "-4일"][:len(dlist)]
        d0 = dlist[0]
        d1 = dlist[1] if len(dlist) > 1 else None

        def dday(dd):
            return s_all[day == dd]

        tot = {dd: dday(dd)[["qty", "gmv"]].sum() for dd in dlist}
        gmv0, qty0 = float(tot[d0]["gmv"]), float(tot[d0]["qty"])
        gmv1 = float(tot[d1]["gmv"]) if d1 is not None else 0.0
        wow = (gmv0 - gmv1) / gmv1 * 100 if gmv1 else None

        s0 = dday(d0)
        store0 = s0.groupby("store_name").agg(qty=("qty", "sum"), gmv=("gmv", "sum")).sort_values("gmv", ascending=False)
        brand0 = s0.groupby("brand_nm").agg(qty=("qty", "sum"), gmv=("gmv", "sum")).sort_values("gmv", ascending=False)
        lead_store = store0.index[0] if len(store0) else "-"
        lead_store_sh = store0["gmv"].iloc[0] / gmv0 * 100 if gmv0 and len(store0) else 0
        lead_brand = brand0.index[0] if len(brand0) else "-"
        lead_brand_sh = brand0["gmv"].iloc[0] / gmv0 * 100 if gmv0 and len(brand0) else 0

        # 재고(상품 단위) + 매장별 점재고 — 캐시된 집계 사용(필터 무관, 1회 계산)
        inv_g = _inv_goods().set_index("goods_no").rename(columns={"점재고합계": "점재고"})
        _gm = _goods_master().set_index("goods_no")   # 상품마스터(bizest.goods): 상품명·브랜드명
        name_map = _gm["goods_nm"]
        brand_map = _gm["brand_nm"]
        inv_store_long = _inv_store_long()

        # 재고보충: (매장,상품) 전일판매>0 + 허브>0 + 점재고<전일판매×2
        sd = s0.groupby(["store_name", "goods_no"]).agg(전일판매=("qty", "sum"), brand_nm=("brand_nm", "first")).reset_index()
        rest = sd.merge(inv_store_long, on=["store_name", "goods_no"], how="left")
        rest = rest.merge(inv_g[["허브합계"]].reset_index(), on="goods_no", how="left")
        rest["goods_nm"] = rest["goods_no"].map(name_map).fillna("")
        rest["점재고"] = rest["점재고"].fillna(0)
        rest["허브합계"] = rest["허브합계"].fillna(0)
        rest = rest[(rest["전일판매"] > 0) & (rest["허브합계"] > 0) & (rest["점재고"] < rest["전일판매"] * 2)]
        rest = rest.sort_values(["점재고", "전일판매"], ascending=[True, False])
        urgent = rest[rest["점재고"] <= 0]

        st.markdown(f"#### 오프라인 MD 일별 매출 리포트 — {pd.Timestamp(d0).date()} 기준")
        st.caption(f"전일 = 데이터 최신일 · 최상위 카테 **{basis}** · 구분 **{seg}** · 사이드바 필터와 무관")

        # 1) 종합 분석
        with st.container(border=True):
            issue = "🔴 이슈" if (wow is not None and wow < -5) else "🟢 양호"
            section("종합 분석", issue)
            k = st.columns(4)
            k[0].metric("전일 GMV", won(gmv0), (f"{wow:+.1f}%" if wow is not None else None))
            k[1].metric("전일 판매수량", num(qty0))
            k[2].metric(f"선두 매장 · {lead_store}", f"{lead_store_sh:.1f}%")
            k[3].metric(f"주도 브랜드 · {lead_brand}", f"{lead_brand_sh:.1f}%")
            st.markdown(
                f"- 전일 GMV **{won(gmv0)}** ({num(qty0)}개 판매)"
                + (f", 전전일 대비 **{wow:+.1f}%**" if wow is not None else "")
                + f"\n- 재고보충 필요 **{len(rest):,}건** · 긴급(점재고 0) **{len(urgent):,}건** — 오늘 중 허브 발주 검토")

        # 2) 액션 포인트
        with st.container(border=True):
            section("오늘 유의할 액션 포인트")
            msgs = []
            if d1 is not None:
                q0 = s0.groupby("store_name")["qty"].sum()
                q1 = dday(d1).groupby("store_name")["qty"].sum()
                chg = pd.DataFrame({"q0": q0, "q1": q1}).fillna(0)
                chg = chg[chg["q1"] >= 3]
                if len(chg):
                    chg["pct"] = (chg["q0"] - chg["q1"]) / chg["q1"] * 100
                    up = chg.sort_values("pct", ascending=False).iloc[0]
                    dn = chg.sort_values("pct").iloc[0]
                    msgs.append(f"📈 **{up.name}** 전일 대비 판매수량 **{up['pct']:+.0f}%** 급등 — 진열·재고 점검 권장")
                    msgs.append(f"📉 **{dn.name}** 전일 대비 판매수량 **{dn['pct']:+.0f}%** 급락 — 원인 파악 필요")
            if len(urgent):
                u = urgent.iloc[0]
                msgs.append(f"🚨 긴급보충 **{len(urgent)}건** (점재고 0 + 전일 판매) — 1순위 [{u['store_name']}] "
                            f"{u['brand_nm']} {str(u['goods_nm'])[:20]} (전일 {int(u['전일판매'])}개 판매)")
            st.markdown("\n".join(f"- {m}" for m in msgs) if msgs else "- 특이 액션 없음")

        # 3) 주목 상품 추이
        with st.container(border=True):
            section("주목 상품 추이", "🔥판매TOP · 📈급등 · 📉급락")
            gd = (s_all[day.isin(dlist)].assign(d=day).groupby(["goods_no", "d"])["qty"].sum()
                  .reset_index().pivot(index="goods_no", columns="d", values="qty").fillna(0))
            for dd in dlist:
                if dd not in gd.columns:
                    gd[dd] = 0
            top5 = gd.sort_values(d0, ascending=False).head(5).index.tolist()
            tag = {g: "🔥 판매TOP" for g in top5}
            if d1 is not None:
                ratio = gd[(gd[d1] >= 2)].copy()
                ratio["g"] = (ratio[d0] - ratio[d1]) / ratio[d1] * 100
                for g in ratio.sort_values("g", ascending=False).head(5).index:
                    tag.setdefault(g, f"📈 급등 {ratio.loc[g, 'g']:+.0f}%")
                for g in ratio.sort_values("g").head(5).index:
                    tag.setdefault(g, f"📉 급락 {ratio.loc[g, 'g']:+.0f}%")
            notable = list(tag.keys())
            nt = gd.loc[notable, dlist].copy()
            nt.columns = dnames
            nt = nt.join(inv_g[["점재고", "허브1000", "허브1700", "MFS"]])
            nt["goods_nm"] = nt.index.map(name_map).fillna("")
            nt.insert(0, "태그", [tag[g] for g in nt.index])
            nt.insert(1, "브랜드", [brand_map.get(g, "") for g in nt.index])
            nt = nt.reset_index().rename(columns={"goods_no": "UID", "goods_nm": "상품명"})
            st.dataframe(add_total(nt, "태그", skip=("UID",)), width="stretch", hide_index=True, height=360, column_config={
                "UID": st.column_config.NumberColumn("UID", format="%d"),
                "점재고": st.column_config.NumberColumn("점재고", format="localized"),
                "허브1000": st.column_config.NumberColumn("허브1000", format="localized"),
                "허브1700": st.column_config.NumberColumn("허브1700", format="localized"),
                "MFS": st.column_config.NumberColumn("MFS", format="localized"),
            })

        # 4) 전일 전체 실적 + 매장별
        with st.container(border=True):
            section("전일 전체 실적", "일자별 추이 (전일~-4일)")
            trend4 = pd.DataFrame({"날짜": [str(pd.Timestamp(dd).date()) for dd in dlist],
                                   "판매수량": [float(tot[dd]["qty"]) for dd in dlist],
                                   "GMV": [float(tot[dd]["gmv"]) for dd in dlist]})
            st.dataframe(add_total(trend4, "날짜"), width="stretch", hide_index=True, column_config={
                "판매수량": st.column_config.NumberColumn("판매수량", format="localized"),
                "GMV": st.column_config.NumberColumn("GMV", format="localized")})
            section("매장별 실적", "전일 기준")
            sp = store0.reset_index()
            sp["비중"] = sp["gmv"] / gmv0 * 100 if gmv0 else 0
            st.dataframe(add_total(sp, "store_name"), width="stretch", hide_index=True, height=360, column_config={
                "store_name": "매장", "qty": st.column_config.NumberColumn("판매수량", format="localized"),
                "gmv": st.column_config.NumberColumn("GMV", format="localized"),
                "비중": st.column_config.ProgressColumn("비중", format="%.1f%%", min_value=0,
                                                       max_value=float(sp["비중"].max() or 100))})

        c1, c2 = st.columns(2)
        with c1:
            with st.container(border=True):
                section("브랜드별 실적 TOP 100")
                bt = brand0.head(100).reset_index()
                bt["비중"] = bt["gmv"] / gmv0 * 100 if gmv0 else 0
                st.dataframe(add_total(bt, "brand_nm"), width="stretch", hide_index=True, height=400, column_config={
                    "brand_nm": "브랜드", "qty": st.column_config.NumberColumn("판매수량", format="localized"),
                    "gmv": st.column_config.NumberColumn("GMV", format="localized"),
                    "비중": st.column_config.NumberColumn("비중", format="%.1f%%")})
        with c2:
            with st.container(border=True):
                section("상품별 실적 TOP 100")
                gt = (s0.groupby("goods_no").agg(qty=("qty", "sum"), gmv=("gmv", "sum"),
                      brand_nm=("brand_nm", "first")).sort_values("gmv", ascending=False).head(100))
                gt["상품명"] = gt.index.map(name_map).fillna("")
                gt["비중"] = gt["gmv"] / gmv0 * 100 if gmv0 else 0
                gt = gt.reset_index().rename(columns={"goods_no": "UID"})
                gt = gt[["brand_nm", "UID", "상품명", "qty", "gmv", "비중"]]
                st.dataframe(add_total(gt, "brand_nm", skip=("UID",)), width="stretch", hide_index=True, height=400, column_config={
                    "brand_nm": "브랜드", "UID": st.column_config.NumberColumn("UID", format="%d"),
                    "qty": st.column_config.NumberColumn("수량", format="localized"),
                    "gmv": st.column_config.NumberColumn("GMV", format="localized"),
                    "비중": st.column_config.NumberColumn("비중", format="%.1f%%")})

        # 5) 재고보충 필요
        with st.container(border=True):
            section("재고보충 필요 상품", f"전체 {len(rest):,}건 · 기준: 허브재고 보유 + 전일판매>0 + 점재고<전일판매×2")
            rr = rest.head(100)[["store_name", "brand_nm", "goods_no", "goods_nm", "전일판매", "점재고", "허브합계"]]
            st.dataframe(add_total(rr, "store_name", skip=("goods_no",), agg=rest), width="stretch", hide_index=True, height=400, column_config={
                "store_name": "매장", "brand_nm": "브랜드",
                "goods_no": st.column_config.NumberColumn("UID", format="%d"), "goods_nm": "상품명",
                "전일판매": st.column_config.NumberColumn("전일판매", format="localized"),
                "점재고": st.column_config.NumberColumn("점재고", format="localized"),
                "허브합계": st.column_config.NumberColumn("허브재고", format="localized")})
            st.download_button("CSV 다운로드 (재고보충 전체)",
                               rest[["store_name", "brand_nm", "goods_no", "goods_nm", "전일판매", "점재고", "허브합계"]]
                               .to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"restock_{pd.Timestamp(d0).date()}.csv", mime="text/csv")

# ============================ 판매 탭 ============================
with tab_sales:
    cur, prev = date_split(sales)
    st.caption(f"기간 {date_from} ~ {date_to}  ·  신장율 = 직전 동기간({prev_from} ~ {prev_to}) 대비 "
               "· 외국인 = 면세(tax refund) 기준")

    if cur.empty:
        st.info("조건에 해당하는 데이터가 없습니다.")
    else:
        c = dict(gmv=cur.gmv.sum(), normal=cur.normal_amt.sum(), qty=cur.qty.sum(),
                 pay=cur.pay.sum(), fg=cur.foreign_gmv.sum())
        p = dict(gmv=prev.gmv.sum(), normal=prev.normal_amt.sum(), qty=prev.qty.sum(),
                 pay=prev.pay.sum(), fg=prev.foreign_gmv.sum())

        with st.container(border=True):
            k = st.columns(4)
            kpi(k[0], "GMV (판매가×수량)", won(c["gmv"]), delta_pct(c["gmv"], p["gmv"]), daily(cur, "gmv"), "gmv", PRIMARY)
            kpi(k[1], "정상가 매출", won(c["normal"]), delta_pct(c["normal"], p["normal"]), daily(cur, "normal_amt"), "normal_amt", VIOLET)
            kpi(k[2], "순판매수량", num(c["qty"]), delta_pct(c["qty"], p["qty"]), daily(cur, "qty"), "qty", "#0ea5e9")
            kpi(k[3], "외국인 매출 (면세)", won(c["fg"]), delta_pct(c["fg"], p["fg"]), daily(cur, "foreign_gmv"), "foreign_gmv", FGN)

        with st.container(border=True):
            k = st.columns(4)
            k[0].metric("실결제액", won(c["pay"]), delta_pct(c["pay"], p["pay"]))
            k[1].metric("평균 할인율", f"{(1-c['gmv']/c['normal'])*100 if c['normal'] else 0:.1f}%")
            k[2].metric("외국인 매출 비중", f"{(c['fg']/c['gmv']*100 if c['gmv'] else 0):.1f}%")
            k[3].metric("거래 상품 수", num(cur['goods_no'].nunique()))

        with st.container(border=True):
            section("GMV 추이", "위탁 / 매입")
            t = cur.copy()
            t["bucket"] = bucketize(t["sales_date"], gran)
            tr = t.groupby(["bucket", "business_type"], as_index=False)["gmv"].sum()
            pchart(area(tr, "bucket", "gmv", "business_type", BIZ_COLORS), width="stretch")

        ct1, ct2, ct3 = st.columns(3)
        with ct1:
            with st.container(border=True):
                section("최상위 카테 비중", "GMV")
                s = cur.groupby("cat_top", as_index=False)["gmv"].sum()
                s = s[s["gmv"] > 0]
                fig = px.pie(s, values="gmv", names="cat_top", hole=.55,
                             color_discrete_sequence=px.colors.qualitative.Set2)
                fig.update_traces(hovertemplate="%{label}<br>%{value:,.0f}원 (%{percent})<extra></extra>")
                fig.update_layout(**PLOT, height=340, margin=dict(l=0, r=0, t=6, b=0), legend_title_text="")
                pchart(fig, width="stretch")
        with ct2:
            with st.container(border=True):
                section("중카테 비중", "GMV · Top 10 + 기타")
                s = cur.groupby("cat_medium", as_index=False)["gmv"].sum()
                s = s[s["gmv"] > 0].sort_values("gmv", ascending=False)
                top = s.head(10)
                etc = s["gmv"].iloc[10:].sum()
                if etc > 0:
                    top = pd.concat([top, pd.DataFrame([{"cat_medium": "기타", "gmv": etc}])], ignore_index=True)
                fig = px.pie(top, values="gmv", names="cat_medium", hole=.55,
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                fig.update_traces(hovertemplate="%{label}<br>%{value:,.0f}원 (%{percent})<extra></extra>")
                fig.update_layout(**PLOT, height=340, margin=dict(l=0, r=0, t=6, b=0), legend_title_text="")
                pchart(fig, width="stretch")
        with ct3:
            with st.container(border=True):
                section("컨셉 비중", "GMV 비중(%)")
                s = cur.groupby("concept", as_index=False)["gmv"].sum()
                s = s[s["gmv"] > 0]
                tot = s["gmv"].sum()
                s["pct"] = (s["gmv"] / tot * 100) if tot else 0
                s = s.sort_values("pct")
                fig = px.bar(s, x="pct", y="concept", orientation="h", text=s["pct"].map(lambda v: f"{v:.1f}%"),
                             labels={"pct": "", "concept": ""})
                fig.update_traces(marker_color=PRIMARY, textposition="outside", cliponaxis=False,
                                  customdata=s[["gmv"]],
                                  hovertemplate="%{y}<br>비중 %{x:.1f}% (%{customdata[0]:,.0f}원)<extra></extra>")
                fig.update_layout(**PLOT, height=340, margin=dict(l=0, r=14, t=6, b=0))
                fig.update_xaxes(visible=False)
                pchart(fig, width="stretch")

        a, b = st.columns(2)
        with a:
            with st.container(border=True):
                section("매장별 GMV", "내국인 / 외국인(면세)")
                pchart(stack_h(cur, "store_name"), width="stretch")
        with b:
            with st.container(border=True):
                section("사업구분별 GMV", "내국인 / 외국인(면세)")
                pchart(stack_v(cur, "business_type"), width="stretch")

        with st.container(border=True):
            section("브랜드 GMV Top 30", "내국인 / 외국인(면세)")
            pchart(stack_h(cur, "brand_nm", topn=30, height=620), width="stretch")

        with st.container(border=True):
            gb = (cur.groupby(["business_type", "brand_nm"], as_index=False)
                    .agg(qty=("qty", "sum"), gmv=("gmv", "sum"), normal_amt=("normal_amt", "sum"),
                         pay=("pay", "sum"), foreign_gmv=("foreign_gmv", "sum"), goods=("goods_no", "nunique")))
            gb = gb[gb["qty"] > 0].copy()
            gb["할인율"] = (1 - gb["gmv"] / gb["normal_amt"].where(gb["normal_amt"] != 0)).fillna(0) * 100
            gb["외국인비중"] = (gb["foreign_gmv"] / gb["gmv"].where(gb["gmv"] != 0)).fillna(0) * 100
            gb = gb.sort_values("gmv", ascending=False)
            section("브랜드별 상세", f"판매 발생 전체 · 총 {len(gb):,}개 브랜드")
            st.dataframe(add_total(gb, "brand_nm"), width="stretch", height=440, hide_index=True, column_config={
                "business_type": "사업구분", "brand_nm": "브랜드",
                "goods": st.column_config.NumberColumn("상품수", format="%d"),
                "qty": st.column_config.NumberColumn("순판매수량", format="localized"),
                "gmv": st.column_config.NumberColumn("GMV", format="localized"),
                "normal_amt": st.column_config.NumberColumn("정상가매출", format="localized"),
                "pay": st.column_config.NumberColumn("실결제", format="localized"),
                "foreign_gmv": st.column_config.NumberColumn("외국인GMV", format="localized"),
                "외국인비중": st.column_config.ProgressColumn("외국인비중", format="%.0f%%", min_value=0, max_value=100),
                "할인율": st.column_config.NumberColumn("할인율", format="%.1f%%"),
            })
            st.download_button("CSV 다운로드 (브랜드)", gb.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_sales_by_brand.csv", mime="text/csv")

        with st.container(border=True):
            g = (cur.groupby(["business_type", "cat_top", "cat_large", "cat_medium", "brand_nm", "goods_no", "goods_nm"], as_index=False)
                    .agg(qty=("qty", "sum"), gmv=("gmv", "sum"), normal_amt=("normal_amt", "sum"),
                         pay=("pay", "sum"), foreign_gmv=("foreign_gmv", "sum")))
            g = g[g["qty"] > 0].copy()
            g["할인율"] = (1 - g["gmv"] / g["normal_amt"].where(g["normal_amt"] != 0)).fillna(0) * 100
            g["외국인비중"] = (g["foreign_gmv"] / g["gmv"].where(g["gmv"] != 0)).fillna(0) * 100
            g = g.sort_values("gmv", ascending=False)
            ig = _inv_goods()
            inv_store_cols = [c for c in ig.columns if c not in ({"goods_no", "goods_nm", "점재고합계", "허브합계"} | set(db.HUB_COLS))]
            g = g.merge(ig.drop(columns=["goods_nm"]), on="goods_no", how="left")   # 상품명은 sales(bizest.goods) 사용
            for c in inv_store_cols + ["점재고합계", "허브합계"] + db.HUB_COLS:
                g[c] = g[c].fillna(0)
            front = ["business_type", "cat_top", "cat_large", "cat_medium", "brand_nm", "goods_no", "goods_nm"]
            g = g[front + [c for c in g.columns if c not in front]]
            section("상품별 상세", f"판매 발생 전체 · 총 {len(g):,}개 상품 · 우측에 매장별/허브 재고")
            cfg = {
                "business_type": st.column_config.Column("사업구분", pinned=True),
                "brand_nm": st.column_config.Column("브랜드", pinned=True),
                "goods_nm": st.column_config.Column("상품명", pinned=True),
                "cat_top": st.column_config.Column("최상위", pinned=True),
                "cat_large": st.column_config.Column("대카테", pinned=True),
                "cat_medium": st.column_config.Column("중카테", pinned=True),
                "goods_no": st.column_config.NumberColumn("상품번호", format="%d", pinned=True),
                "qty": st.column_config.NumberColumn("순판매수량", format="localized"),
                "gmv": st.column_config.NumberColumn("GMV", format="localized"),
                "normal_amt": st.column_config.NumberColumn("정상가매출", format="localized"),
                "pay": st.column_config.NumberColumn("실결제", format="localized"),
                "foreign_gmv": st.column_config.NumberColumn("외국인GMV", format="localized"),
                "외국인비중": st.column_config.ProgressColumn("외국인비중", format="%.0f%%", min_value=0, max_value=100),
                "할인율": st.column_config.NumberColumn("할인율", format="%.1f%%"),
                "점재고합계": st.column_config.NumberColumn("점재고합계", format="localized"),
                "허브합계": st.column_config.NumberColumn("허브합계", format="localized"),
            }
            for c in inv_store_cols + db.HUB_COLS:
                cfg[c] = st.column_config.NumberColumn(c, format="localized")
            st.dataframe(add_total(g, "brand_nm", skip=("goods_no",)), width="stretch", height=440, hide_index=True, column_config=cfg)
            st.download_button("CSV 다운로드 (판매+재고)", g.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_sales_by_goods.csv", mime="text/csv")

# ============================ 고객·외국인 탭 ============================
with tab_cust:
    cur_s, prev_s = date_split(sales)                      # 외국인 = MOSS (판매탭과 동일 기준, 전 필터 반영)
    cur_c, _ = date_split(_customer(), store_only=True)  # 인구통계 = 고객요약 (기간·매장만)
    st.caption("외국인 = 면세(tax refund) · **판매 탭(MOSS)과 동일 기준 · 모든 필터 반영** · "
               "성별/연령/회원 분포는 고객요약 테이블 기준(기간·매장·매장타입만 반영)")
    if cur_s.empty:
        st.info("조건에 해당하는 데이터가 없습니다.")
    else:
        gmv, fgmv = cur_s.gmv.sum(), cur_s.foreign_gmv.sum()
        pg, pf = prev_s.gmv.sum(), prev_s.foreign_gmv.sum()
        with st.container(border=True):
            k = st.columns(4)
            kpi(k[0], "총 GMV", won(gmv), delta_pct(gmv, pg), daily(cur_s, "gmv"), "gmv", PRIMARY)
            kpi(k[1], "외국인(면세) 매출", won(fgmv), delta_pct(fgmv, pf), daily(cur_s, "foreign_gmv"), "foreign_gmv", FGN)
            k[2].metric("외국인 매출 비중", f"{(fgmv/gmv*100 if gmv else 0):.1f}%")
            k[3].metric("순판매수량", num(cur_s.qty.sum()), delta_pct(cur_s.qty.sum(), prev_s.qty.sum()))

        with st.container(border=True):
            section("외국인(면세) 매출 추이")
            t = cur_s.copy()
            t["bucket"] = bucketize(t["sales_date"], gran)
            tr = t.groupby("bucket", as_index=False)["foreign_gmv"].sum()
            pchart(area(tr, "bucket", "foreign_gmv").update_traces(
                line_color=FGN, fillcolor=_rgba(FGN, .2)), width="stretch")

        a, b = st.columns(2)
        with a:
            with st.container(border=True):
                section("매장별 외국인 매출")
                s = cur_s.groupby("store_name", as_index=False)["foreign_gmv"].sum().sort_values("foreign_gmv")
                pchart(hbar(s, "foreign_gmv", "store_name", color=FGN), width="stretch")
        with b:
            with st.container(border=True):
                section("매장별 외국인 비중")
                s = cur_s.groupby("store_name", as_index=False).agg(g=("gmv", "sum"), f=("foreign_gmv", "sum"))
                s["ratio"] = (s["f"] / s["g"].where(s["g"] != 0)).fillna(0) * 100
                s = s.sort_values("ratio")
                s["label"] = s["ratio"].map(lambda v: f"{v:.0f}%")
                fig = px.bar(s, x="ratio", y="store_name", orientation="h", text="label", labels={"ratio": "", "store_name": ""})
                fig.update_traces(marker_color=FGN, textposition="outside", cliponaxis=False, hovertemplate="%{y}<br>%{x:.1f}%<extra></extra>")
                fig.update_layout(**PLOT, height=430, margin=dict(l=0, r=14, t=6, b=0))
                fig.update_xaxes(visible=False)
                pchart(fig, width="stretch")

        st.markdown("###### 고객 인구통계 — 고객요약 기준 (기간·매장·매장타입만 반영)")
        if cur_c.empty:
            st.info("고객요약 데이터가 없습니다.")
        else:
            a, b, c2 = st.columns(3)
            with a:
                with st.container(border=True):
                    section("성별 비중", "기타 제외")
                    s = cur_c.groupby("sex", as_index=False)["gmv"].sum()
                    s = s[s["sex"] != "기타"]
                    fig = px.pie(s, values="gmv", names="sex", hole=.55, color="sex", color_discrete_map=SEX_COLORS)
                    fig.update_layout(**PLOT, height=320, margin=dict(l=0, r=0, t=6, b=0), legend_title_text="")
                    pchart(fig, width="stretch")
            with b:
                with st.container(border=True):
                    section("연령대 비중", "기타 제외")
                    s = cur_c.groupby("age_band", as_index=False)["gmv"].sum()
                    s = s[s["age_band"] != "기타"]
                    tot = s["gmv"].sum()
                    s["비중"] = (s["gmv"] / tot * 100) if tot else 0
                    order = [a for a in AGE_ORDER if a in set(s["age_band"])]
                    s["age_band"] = pd.Categorical(s["age_band"], order, ordered=True)
                    s = s.sort_values("age_band")
                    fig = px.bar(s, x="age_band", y="비중", text=s["비중"].map(lambda v: f"{v:.1f}%"),
                                 labels={"age_band": "", "비중": "비중(%)"})
                    fig.update_traces(marker_color=PRIMARY, textposition="outside", cliponaxis=False,
                                      customdata=s[["gmv"]],
                                      hovertemplate="%{x}<br>비중 %{y:.1f}%<br>%{customdata[0]:,.0f}원<extra></extra>")
                    fig.update_layout(**PLOT, height=320, margin=dict(l=0, r=0, t=16, b=0))
                    pchart(fig, width="stretch")
            with c2:
                with st.container(border=True):
                    section("회원 / 비회원")
                    s = cur_c.groupby("member", as_index=False)["gmv"].sum()
                    fig = px.pie(s, values="gmv", names="member", hole=.55,
                                 color_discrete_sequence=[PRIMARY, SLATE, "#c7d2fe"])
                    fig.update_layout(**PLOT, height=320, margin=dict(l=0, r=0, t=6, b=0), legend_title_text="")
                    pchart(fig, width="stretch")

# ============================ 재고 탭 ============================
with tab_inv:
    inv = _inv_pivot()
    meta_hub = {"barcode", "goods_no", "goods_opt", "brand_nm", "goods_nm", "business_type",
                "cat_top", "cat_large", "cat_medium", "off_md_id", "concept",
                "company_id", "brand_id",
                "점재고합계", "허브합계", *db.HUB_COLS}
    store_cols = [c for c in inv.columns if c not in meta_hub]
    st.caption("최신 스냅샷 · 상품·옵션(barcode) 단위 · 창고: MFS / 허브1000(plant1000 20xx) / "
               "허브1700(plant1700 2000) · 기간 필터 미적용")

    iv = inv.copy()
    if f_biz:
        iv = iv[iv["business_type"].isin(f_biz)]
    if f_brand:
        iv = iv[iv["brand_nm"].isin(f_brand)]
    for col, sel in (("cat_top", f_cat_top), ("cat_large", f_cat_large), ("cat_medium", f_cat_medium)):
        if sel:
            iv = iv[iv[col].isin(sel)]
    if f_concept:
        iv = iv[iv["concept"].isin(f_concept)]
    if f_md:
        iv = iv[iv["off_md_id"].replace("", "(미지정)").isin(f_md)]
    if f_goods:
        iv = iv[iv["goods_no"].isin(f_goods)]
    iv = iv[iv["goods_nm"].astype(str).str.strip() != ""]   # 상품명 null/공백 제거

    order = [s for s in store_df["store_name"].tolist() if s in store_cols]
    if f_store:
        vis = [s for s in order if s in f_store]
    elif f_type:
        allowed = set(store_df[store_df["shop_type"].isin(f_type)]["store_name"])
        vis = [s for s in order if s in allowed]
    else:
        vis = order

    iv["점재고합계"] = iv[vis].sum(axis=1) if vis else 0
    iv = iv[(iv["점재고합계"] > 0) | (iv["허브합계"] > 0)]

    if iv.empty:
        st.info("조건에 해당하는 재고가 없습니다.")
    else:
        with st.container(border=True):
            k = st.columns(4)
            k[0].metric("점재고 합계 (선택 매장)", num(iv["점재고합계"].sum()))
            k[1].metric("창고(허브) 합계", num(iv["허브합계"].sum()))
            k[2].metric("옵션 수 (barcode)", num(len(iv)))
            k[3].metric("상품 수 (goods)", num(iv["goods_no"].nunique()))

        with st.container(border=True):
            section("매장별 재고수량", "사업구분(위탁/매입) 누적")
            g = iv.groupby("business_type")[vis].sum() if vis else pd.DataFrame()
            if g.empty or float(g.values.sum()) == 0:
                st.info("표시할 매장 점재고가 없습니다.")
            else:
                tot = g.sum(axis=0)                                  # store → 총 점재고
                order_stores = tot.sort_values().index.tolist()
                sdf = (g.T.reset_index().rename(columns={"index": "store"})
                        .melt(id_vars="store", var_name="사업구분", value_name="qty"))
                sdf = sdf[sdf["qty"] > 0]
                fig = px.bar(sdf, x="qty", y="store", color="사업구분", orientation="h",
                             color_discrete_map=BIZ_COLORS,
                             category_orders={"store": order_stores, "사업구분": ["위탁", "매입"]},
                             labels={"qty": "", "store": "", "사업구분": ""})
                fig.update_traces(hovertemplate="%{y} · %{fullData.name}<br>%{x:,.0f}<extra></extra>")
                fig.update_layout(**PLOT, height=430, margin=dict(l=0, r=66, t=6, b=0), barmode="stack",
                                  legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title=""))
                fig.update_xaxes(visible=False)
                for stn in order_stores:                             # 막대 끝 총합 라벨
                    fig.add_annotation(x=float(tot[stn]), y=stn, text=num(float(tot[stn])),
                                       showarrow=False, xanchor="left", xshift=4,
                                       font=dict(size=11, color=SLATE))
                pchart(fig, width="stretch")

        with st.container(border=True):
            CAP = 3000
            disp = iv.assign(_tot=iv["점재고합계"] + iv["허브합계"]).sort_values("_tot", ascending=False)
            cols = ["brand_nm", "goods_nm", "goods_no", "goods_opt", "business_type",
                    "cat_top", "cat_large", "cat_medium",
                    "점재고합계"] + vis + db.HUB_COLS + ["허브합계"]
            disp = disp[cols]
            shown = disp.head(CAP)
            section("상품·옵션 × 매장 / 창고 재고",
                    f"총 {len(iv):,}개 옵션 · 화면 표시 상위 {len(shown):,}개(재고순) · 전체는 CSV / 브랜드 필터로 좁히기")
            cfg = {
                "brand_nm": st.column_config.Column("브랜드", pinned=True),
                "goods_nm": st.column_config.Column("상품명", pinned=True),
                "goods_no": st.column_config.NumberColumn("UID", format="%d", pinned=True),
                "goods_opt": st.column_config.Column("옵션", pinned=True),
                "business_type": st.column_config.Column("사업구분", pinned=True),
                "cat_top": st.column_config.Column("최상위", pinned=True),
                "cat_large": st.column_config.Column("대카테", pinned=True),
                "cat_medium": st.column_config.Column("중카테", pinned=True),
                "점재고합계": st.column_config.NumberColumn("점재고합계", format="localized"),
                "허브합계": st.column_config.NumberColumn("허브합계", format="localized"),
            }
            for c in vis + db.HUB_COLS:
                cfg[c] = st.column_config.NumberColumn(c, format="localized")
            st.dataframe(add_total(shown, "brand_nm", skip=("goods_no",), agg=disp), width="stretch", height=560, hide_index=True, column_config=cfg)
            st.download_button("CSV 다운로드 (재고 피벗 전체)", disp.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_inventory_pivot.csv", mime="text/csv")

# ============================ 비교·신장율 탭 ============================
with tab_cmp:
    st.caption("기준일 기준 GMV 신장율 — 전일비 · 전주비 · 전월비 · 전년비 (모두 전기 같은 경과일 누적=동기 기준). "
               "사이드바 필터 적용, 날짜만 기준일로 대체. 비교 데이터 없으면 X.")
    cc1, cc2 = st.columns([1, 3])
    with cc1:
        ref = st.date_input("기준일", value=dmax, min_value=dmin, max_value=dmax, key="cmp_ref")
    with cc2:
        clv = st.radio("카테고리 레벨", ["최상위", "대카테", "중카테"], index=1, horizontal=True, key="cmp_clv")

    def _ok(d):
        return dmin <= d <= dmax

    def _range(a, b):
        return [d.date() for d in pd.date_range(a, b)]

    iso = ref.isocalendar()
    _wn = ["월", "화", "수", "목", "금", "토", "일"][iso.weekday - 1]
    d_dod = ref - dt.timedelta(days=1)
    wk_start = ref - dt.timedelta(days=iso.weekday - 1)                 # ISO 주 월요일
    mstart = ref.replace(day=1)
    pmstart = (mstart - dt.timedelta(days=1)).replace(day=1)
    prev_last = mstart - dt.timedelta(days=1)
    prev_end = min(pmstart + dt.timedelta(days=(ref - mstart).days), prev_last)
    ystart = dt.date(ref.year, 1, 1)
    # 8개 GMV 윈도우 (당기 + 전기 동기). 전체비/전기 전체 윈도우는 기간 중도엔 오해 소지가 커서 제외.
    WINS = {
        "기준일gmv": {ref},
        "전일gmv": {d_dod},
        "당주gmv": set(_range(wk_start, ref)),                                   # WTD
        "전주wtd": set(_range(wk_start - dt.timedelta(days=7), ref - dt.timedelta(days=7))),   # 전주 동기
        "당월gmv": set(_range(mstart, ref)),                                     # MTD
        "전월wtd": set(_range(pmstart, prev_end)),                               # 전월 동기(경과일)
        "당년gmv": set(_range(ystart, ref)),                                     # YTD
        "전년wtd": set(_range(ystart - dt.timedelta(days=364), ref - dt.timedelta(days=364))),  # 전년 동기(−364)
    }
    v_wk = _ok(wk_start - dt.timedelta(days=7))
    v_mo = _ok(pmstart)
    v_yr_s = _ok(ystart - dt.timedelta(days=364))
    # 비율 정의: (라벨, 당기윈도우, 기준윈도우, 유효) — 모두 동기(같은 경과일 누적) 기준
    RATIOS = [
        ("전일비", "기준일gmv", "전일gmv", _ok(d_dod)),
        ("전주비", "당주gmv", "전주wtd", v_wk),
        ("전월비", "당월gmv", "전월wtd", v_mo),
        ("전년비", "당년gmv", "전년wtd", v_yr_s),
    ]
    VAL_COLS = list(WINS)
    RATIO_COLS = [r[0] for r in RATIOS]
    COLS = ["기준일gmv", "전일gmv", "전일비", "당주gmv", "전주wtd", "전주비",
            "당월gmv", "전월wtd", "전월비", "당년gmv", "전년wtd", "전년비"]

    st.caption(f"기준일 {ref} (ISO {iso.year}-W{iso.week:02d} {_wn}) · 전주 {wk_start}~{ref}{'' if v_wk else ' (전주X)'} · "
               f"전월 {mstart}~{ref} vs {pmstart}~{prev_end}{'' if v_mo else ' (X)'} · "
               f"전년 {ystart}~{ref} vs 전년동기(−364){'' if v_yr_s else ' (X)'}")

    reg_map = _goods_master().set_index("goods_no")["reg_date"]
    new_start = pd.Timestamp(ref) - pd.Timedelta(days=90)

    # 윈도우별 GMV를 fcmp에 1회 벡터 계산 (키 무관) → 표마다 groupby 1번
    fcmp = sales[sales_mask(sales)].copy()
    fcmp["d"] = fcmp["sales_date"].dt.date
    for w, ds in WINS.items():
        fcmp[w] = fcmp["gmv"].where(fcmp["d"].isin(ds), 0.0)
    WT = {w: float(fcmp[w].sum()) for w in WINS}     # 전체 합계(요약용)

    def pct(c, b, valid, newok=True):
        if not valid:
            return "X"
        if b == 0:
            return "신규" if (c > 0 and newok) else "—"
        return f"{(c - b) / b * 100:+.1f}%"

    def _color(v):
        if isinstance(v, str):
            if v.endswith("%"):
                return "color:#16a34a;font-weight:600" if v.startswith("+") else "color:#dc2626;font-weight:600"
            if v == "신규":
                return "color:#2563eb;font-weight:600"
            if v in ("X", "—"):
                return "color:#9ca3af"
        return ""

    def show_cmp(df, height):
        sty = (df.style.map(_color, subset=RATIO_COLS)
               .format({c: "{:,.0f}" for c in VAL_COLS}))
        pin = [c for c in df.columns if c not in VAL_COLS and c not in RATIO_COLS]   # 정보값 열 고정
        cfg = {c: st.column_config.Column(pinned=True) for c in pin}
        st.dataframe(sty, width="stretch", hide_index=True, height=height, column_config=cfg)

    def build_cmp(keys, names, reg=None, cap=None):
        agg = fcmp.groupby(keys)[VAL_COLS].sum()
        sel = agg[agg["기준일gmv"] > 0].sort_values("기준일gmv", ascending=False)
        if sel.empty:
            return None
        idx = list(sel.index)
        is_prod = reg is not None and "goods_no" in keys
        gpos = keys.index("goods_no") if is_prod else -1

        def _new(k):
            if not is_prod:
                return True
            g = k[gpos] if isinstance(k, tuple) else k
            rd = reg.get(g)
            return bool(pd.notna(rd) and rd >= new_start)
        newflags = [_new(k) for k in idx]
        disp = sel.reset_index().rename(columns=dict(zip(keys, names)))
        if "UID" in disp.columns:
            disp["UID"] = disp["UID"].astype("Int64").astype(str)
        for label, cw, bw, valid in RATIOS:
            cv, bv = sel[cw].values, sel[bw].values
            disp[label] = [pct(cv[i], bv[i], valid, newflags[i]) for i in range(len(idx))]
        # 합계 행 (전체 sel 기준)
        tot = {c: "" for c in disp.columns}
        tot[names[0]] = "합계"
        for w in VAL_COLS:
            tot[w] = float(sel[w].sum())
        for label, cw, bw, valid in RATIOS:
            tot[label] = pct(float(sel[cw].sum()), float(sel[bw].sum()), valid, True)
        disp = disp[[*names] + COLS]
        out = pd.concat([pd.DataFrame([tot]), disp], ignore_index=True)
        return out.head(cap + 1) if cap else out

    # ---- 최상단 요약 ----
    with st.container(border=True):
        section("비교 요약", f"기준일 {ref} · 전체(필터 적용) 기준 · 카드 델타=동기비")
        if WT["기준일gmv"] <= 0 and WT["당월gmv"] <= 0:
            st.info("기준일에 해당하는 매출 데이터가 없습니다. 기준일/필터를 조정하세요.")
        else:
            m = st.columns(4)
            m[0].metric("기준일 GMV", won(WT["기준일gmv"]), pct(WT["기준일gmv"], WT["전일gmv"], _ok(d_dod)))
            m[1].metric("당주 GMV (WTD)", won(WT["당주gmv"]), pct(WT["당주gmv"], WT["전주wtd"], v_wk))
            m[2].metric("당월 GMV (MTD)", won(WT["당월gmv"]), pct(WT["당월gmv"], WT["전월wtd"], v_mo))
            m[3].metric("당년 GMV (YTD)", won(WT["당년gmv"]), pct(WT["당년gmv"], WT["전년wtd"], v_yr_s))
            st.caption(f"각 카드 델타 = 동기비(전일비/전주비/전월비/전년비, 전기 같은 경과일 누적 대비). "
                       f"전일비 {pct(WT['기준일gmv'], WT['전일gmv'], _ok(d_dod))}")

    clv_map = {"최상위": ("cat_top", "최상위카테"), "대카테": ("cat_large", "대카테"), "중카테": ("cat_medium", "중카테")}
    ckey, cname = clv_map[clv]

    SUB = "전일/전주/전월/전년 GMV 신장율 (동기 기준)"

    with st.container(border=True):
        section("매장별 비교", f"기준일({ref}) 매출 발생 매장 · {SUB}")
        sto = build_cmp(["store_name"], ["매장"])
        if sto is None:
            st.info("기준일에 해당하는 매출 데이터가 없습니다.")
        else:
            show_cmp(sto, 460)
            st.download_button("CSV 다운로드 (매장 비교)", sto.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_compare_store.csv", mime="text/csv", key="cmp_sto_dl")

    with st.container(border=True):
        section("카테고리별 비교", f"{clv} 기준 · 기준일({ref}) 매출 발생분 · {SUB}")
        cat = build_cmp([ckey], [cname])
        if cat is None:
            st.info("기준일에 해당하는 매출 데이터가 없습니다.")
        else:
            show_cmp(cat, 460)
            st.download_button("CSV 다운로드 (카테고리 비교)", cat.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_compare_category.csv", mime="text/csv", key="cmp_cat_dl")

    with st.container(border=True):
        section("브랜드별 비교", f"기준일({ref}) 매출 발생 브랜드 · {SUB}")
        brd = build_cmp(["brand_nm"], ["브랜드"], cap=200)
        if brd is None:
            st.info("기준일에 해당하는 매출 데이터가 없습니다.")
        else:
            show_cmp(brd, 460)
            st.download_button("CSV 다운로드 (브랜드 비교)", brd.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_compare_brand.csv", mime="text/csv", key="cmp_brd_dl")

    with st.container(border=True):
        section("상품별 비교", f"기준일({ref}) 매출 발생 상위 300 · {SUB}")
        st.caption("비교기간 무판매→기준일 판매 셀: 🔵신규=상품 등록(reg_dm) 90일 이내 신상품 · '—'=그 외.")
        prod = build_cmp(["brand_nm", "goods_no", "goods_nm", "cat_top", "cat_large", "cat_medium"],
                         ["브랜드", "UID", "상품명", "최상위", "대카테", "중카테"], reg=reg_map, cap=300)
        if prod is None:
            st.info("기준일에 해당하는 매출 데이터가 없습니다.")
        else:
            show_cmp(prod, 560)
            st.download_button("CSV 다운로드 (상품 비교 전체)", prod.to_csv(index=False).encode("utf-8-sig"),
                               file_name="offline_compare_goods.csv", mime="text/csv", key="cmp_goods_dl")
