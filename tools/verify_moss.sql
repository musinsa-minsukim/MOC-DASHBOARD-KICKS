-- MOSS 오프라인 판매 검증 불변식 (2026-06-21)
-- 기대값: net = 765,922,600 / qty = 20,649
--   * qty 는 영수증수가 아니라 순판매수량(SUM(sgn*quantity)). 같은 날 영수증수는 9,366건.
--   * 매장 필터(오프라인 4종) 필수 + shop_no 19(뷰티 스페이스 1) 제외.
--     19 포함 시 766,039,700 / 20,658, 매장 필터 누락 시 MOSS 전체 2,222,505,896 / 36,034.
-- 실행: python tools/dbx.py tools/verify_moss.sql
WITH dim AS (
  SELECT DISTINCT CAST(shop_no AS INT) AS shop_no
  FROM team.sales.offline_sales_mart_v
  WHERE shop_type IN ('selectshop','kicks','beauty','outlet')
),
ord AS (
  SELECT om.shop_no, 1 AS sgn, oo.quantity, oo.order_amount
  FROM ocmp.moss.order_option oo
  JOIN ocmp.moss.order_master om ON om.order_id = oo.order_id
  WHERE om.dummy_order = 0 AND om.order_status = 50
    AND CAST(COALESCE(om.transaction_at, om.created_at) AS DATE) = '2026-06-21'
),
refclaim AS (
  SELECT order_id FROM ocmp.moss.claim
  WHERE claim_type = 'REFUND' AND CAST(created_at AS DATE) = '2026-06-21'
  GROUP BY order_id
),
ref AS (
  SELECT om.shop_no, -1 AS sgn, oo.quantity, oo.order_amount
  FROM refclaim rc
  JOIN ocmp.moss.order_master om ON om.order_id = rc.order_id
  JOIN ocmp.moss.order_option oo ON oo.order_id = rc.order_id
  WHERE om.dummy_order = 0
),
lines AS (SELECT * FROM ord UNION ALL SELECT * FROM ref)
SELECT CAST(SUM(l.sgn * l.order_amount) AS BIGINT) AS net,
       CAST(SUM(l.sgn * l.quantity)     AS BIGINT) AS qty,
       CASE WHEN SUM(l.sgn * l.order_amount) = 765922600
             AND SUM(l.sgn * l.quantity) = 20649 THEN 'PASS' ELSE 'FAIL' END AS verdict
FROM lines l
JOIN dim d ON d.shop_no = l.shop_no
WHERE l.shop_no <> 19
