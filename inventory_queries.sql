-- =====================================================================
-- 오프라인 재고 조회 쿼리 (대시보드 db.load_inventory_pivot 기준, 2026-06 최신)
-- 정의: 위탁 = SCM-HUB(=MFS) / 매입 = 플랜트1000(2000·2010·2020·2060) + 플랜트1700(2000) + 오프라인 매장재고
-- 키: barcode (옵션 단위). 점재고 = 매장 sellable, 허브 = 창고 wqty/MFS.
-- 공통 매장차원(dim_store): selectshop/kicks/beauty/outlet, shop_no=90→RUN.
-- =====================================================================


-- ① 매장 점재고 (editorial 최신 스냅샷) : barcode × 매장
WITH dim_store AS (
  SELECT t.shop_no,
         CASE WHEN t.shop_no = 90 THEN 'RUN' ELSE UPPER(t.shop_type) END AS shop_type,
         REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_type, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no
),
l AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary)
SELECT s.barcode,
       ANY_VALUE(s.goods_no)  AS goods_no,
       ANY_VALUE(s.goods_opt) AS goods_opt,
       ANY_VALUE(s.brand_nm)  AS brand_nm,
       ANY_VALUE(s.goods_nm)  AS goods_nm,
       ANY_VALUE(CASE s.ord_com_type WHEN '입점(위탁)' THEN '위탁'
                                     WHEN '공급(매입)' THEN '매입' ELSE '기타' END) AS business_type,
       st.store_name,
       CAST(SUM(s.sellable_qty) AS DOUBLE) AS 점재고
FROM team.sales.dsh_d_upt_editorial_stock_summary s
JOIN l        ON s.ord_state_date = l.d
JOIN dim_store st ON st.shop_no = s.shop_no
WHERE s.barcode IS NOT NULL
GROUP BY s.barcode, st.store_name;


-- ② 플랜트1700(트레이딩) 매장 재고 (sap_inventory) — editorial에 없어 별도, 매입 매장재고로 합산
WITH dim_store AS (
  SELECT t.shop_no,
         CASE WHEN t.shop_no = 90 THEN 'RUN' ELSE UPPER(t.shop_type) END AS shop_type,
         REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_type, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no
)
SELECT sap.barcode, ds.store_name,
       ANY_VALUE(sap.goods_no)    AS goods_no,
       ANY_VALUE(sap.option_name) AS goods_opt,
       '매입' AS business_type,
       CAST(SUM(sap.available_stock) AS DOUBLE) AS 점재고
FROM ocmp.moss.sap_inventory sap
JOIN ocmp.moss.shop_location sl
  ON sl.erp_plant_code = sap.plant_code AND sl.erp_location_code = sap.storage_location
JOIN dim_store ds ON ds.shop_no = CAST(sl.shop_no AS INT)
WHERE sap.plant_code = '1700' AND sap.barcode IS NOT NULL
GROUP BY sap.barcode, ds.store_name;


-- ③ 창고/허브 재고 : 허브1000(매입 플랜트1000) + 허브1700(매입 플랜트1700) + MFS(위탁 SCM-HUB)
--    ★ 허브1000은 지정 창고만(2000·2010·2020·2060). LIKE '20%'는 신규창고(2011~/2040 등) 과대계상 → 수정됨.
WITH dim_store AS (
  SELECT t.shop_no,
         CASE WHEN t.shop_no = 90 THEN 'RUN' ELSE UPPER(t.shop_type) END AS shop_type,
         REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_type, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no
),
l  AS (SELECT MAX(ord_state_date) d FROM team.sales.dsh_d_upt_editorial_stock_summary),
bc AS (SELECT DISTINCT s.barcode FROM team.sales.dsh_d_upt_editorial_stock_summary s
       JOIN l ON s.ord_state_date = l.d JOIN dim_store st ON st.shop_no = s.shop_no
       WHERE s.barcode IS NOT NULL),
le AS (SELECT MAX(dt) d FROM team.partner.raw_erp_stock),
erp1700 AS (SELECT DISTINCT barcode FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
            WHERE r.store_cd='1700' AND r.lgort='2000' AND r.barcode IS NOT NULL),
hubmap AS (
  SELECT sku_id, supplier_barcode FROM (
    SELECT sku_id, supplier_barcode,
           ROW_NUMBER() OVER (PARTITION BY sku_id ORDER BY strd_dt DESC) rn
    FROM team.scm.scm_hub_goods_meta WHERE supplier_barcode IS NOT NULL) WHERE rn = 1)
SELECT r.barcode AS barcode, '허브1000' AS hub, CAST(SUM(r.wqty) AS DOUBLE) AS qty
  FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
  WHERE r.store_cd='1000' AND r.lgort IN ('2000','2010','2020','2060')
    AND r.barcode IN (SELECT barcode FROM bc)
  GROUP BY r.barcode
UNION ALL
SELECT r.barcode, '허브1700', CAST(SUM(r.wqty) AS DOUBLE)
  FROM team.partner.raw_erp_stock r JOIN le ON r.dt = le.d
  WHERE r.store_cd='1700' AND r.lgort='2000' AND r.barcode IS NOT NULL
  GROUP BY r.barcode
UNION ALL
SELECT h.supplier_barcode, 'MFS', CAST(SUM(f.mfs_stock_qty) AS DOUBLE)
  FROM team.commercepm.mfs_stock_daily f JOIN hubmap h ON h.sku_id = f.sku_id
  WHERE h.supplier_barcode IN (SELECT barcode FROM bc UNION SELECT barcode FROM erp1700)
  GROUP BY h.supplier_barcode;
