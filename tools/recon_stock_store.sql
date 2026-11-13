-- 재고 이행 대사(매장 × 사업구분): 레거시 editorial 점재고(D-1 마감) ↔ 신규 통합원장 team.scm.stock(매시).
--   실행: python tools/dbx.py tools/recon_stock_store.sql --max-rows 100
--   범위: 대시보드 dim_store(오프라인 4종 매장). 비교 단위 = 매장 × goods_no (pairs / pairs_eq).
--   열: legacy_editorial(=sellable_qty 합) / new_total(=total_quantity 합, 원장 그대로) /
--       new_clamped(SKU×매장 음수 0처리) / new_remaining(실가용) / sap_erp(ERP 매장재고, 매입 참고치).
--   매핑 대표 플래그 = is_rep_uid_option_code_musinsa (2026-11-12 열 이름 변경, SKU당 1행). DISTINCT 는 안전장치.
WITH dim_store AS (
  SELECT t.shop_no,
    REGEXP_REPLACE(TRIM(COALESCE(sid.shop_name, t.shop_nm)), ' +', ' ') AS store_name
  FROM (SELECT DISTINCT CAST(shop_no AS INT) AS shop_no, shop_nm
        FROM team.sales.offline_sales_mart_v
        WHERE shop_type IN ('selectshop','kicks','beauty','outlet')) t
  LEFT JOIN team.commercepm.offline_shopno_storageid sid ON CAST(sid.shop_no AS INT) = t.shop_no),
l AS (SELECT MAX(ord_state_date_st) d FROM team.sales.dsh_d_upt_editorial_stock_summary
      WHERE ord_state_date_st >= date_format(date_sub(current_date(), 7), 'yyyyMMdd')),
mp AS (SELECT DISTINCT sku_id, uid FROM team.scm.mst_sku_mapping_uid_option_code
       WHERE platform = 'MUSINSA' AND is_rep_uid_option_code_musinsa = TRUE),
leg AS (
  SELECT s.shop_no, CAST(s.goods_no AS BIGINT) AS goods_no,
         MAX(CASE s.ord_com_type WHEN '입점(위탁)' THEN '위탁' WHEN '공급(매입)' THEN '매입' ELSE '기타' END) AS biz,
         SUM(s.sellable_qty) AS q
  FROM team.sales.dsh_d_upt_editorial_stock_summary s
  JOIN l ON s.ord_state_date_st = l.d
  JOIN dim_store d ON d.shop_no = s.shop_no
  WHERE s.barcode IS NOT NULL
  GROUP BY 1, 2),
nw AS (
  SELECT d.shop_no, m.uid AS goods_no,
         SUM(s.total_quantity) AS q, SUM(GREATEST(s.total_quantity, 0)) AS q_pos, SUM(s.remaining_quantity) AS rem
  FROM team.scm.stock s
  JOIN dim_store d ON CAST(d.shop_no AS STRING) = s.root_node_id
  LEFT JOIN mp m ON m.sku_id = s.sku_id
  WHERE s.namespace = 'STORE'
  GROUP BY 1, 2),
sap AS (
  SELECT CAST(shop_no AS INT) AS shop_no, CAST(goods_no AS BIGINT) AS goods_no, SUM(available_stock) AS q
  FROM ocmp.moss.sap_inventory GROUP BY 1, 2),
k AS (SELECT shop_no, goods_no FROM leg UNION SELECT shop_no, goods_no FROM nw)
SELECT
  COALESCE(l2.biz, CASE c.margin_type WHEN 'WONGA' THEN '매입' WHEN 'FEE' THEN '위탁' ELSE '기타' END) AS `구분`,
  k.shop_no, MAX(d.store_name) AS `매장`,
  SUM(COALESCE(l2.q, 0))     AS legacy_editorial,
  SUM(COALESCE(n.q, 0))      AS new_total,
  SUM(COALESCE(n.q_pos, 0))  AS new_clamped,
  SUM(COALESCE(n.rem, 0))    AS new_remaining,
  SUM(COALESCE(sp.q, 0))     AS sap_erp,
  ROUND((SUM(COALESCE(n.q_pos, 0)) - SUM(COALESCE(l2.q, 0))) * 100.0 / NULLIF(SUM(COALESCE(l2.q, 0)), 0), 1) AS `차이율_clamped`,
  COUNT(*) AS pairs,
  SUM(CASE WHEN COALESCE(l2.q, 0) = COALESCE(n.q, 0) THEN 1 ELSE 0 END) AS pairs_eq,
  SUM(ABS(COALESCE(n.q, 0) - COALESCE(l2.q, 0))) AS abs_diff
FROM k
JOIN dim_store d ON d.shop_no = k.shop_no
LEFT JOIN leg l2 ON l2.shop_no = k.shop_no AND l2.goods_no <=> k.goods_no
LEFT JOIN nw n   ON n.shop_no  = k.shop_no AND n.goods_no  <=> k.goods_no
LEFT JOIN sap sp ON sp.shop_no = k.shop_no AND sp.goods_no <=> k.goods_no
LEFT JOIN musinsa.bizest.goods g          ON CAST(g.goods_no AS BIGINT) = k.goods_no
LEFT JOIN musinsa.partnerportal.company c ON c.com_id = g.com_id
GROUP BY 1, 2
ORDER BY 1, 2
