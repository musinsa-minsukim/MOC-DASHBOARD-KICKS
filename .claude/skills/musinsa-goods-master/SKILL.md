---
name: musinsa-goods-master
description: >-
  Look up MUSINSA product master + current online price + 대표이미지·판매웹링크 for a goods_no
  (사용자가 "UID"라 부르는 값) from Databricks — 상품명 / 브랜드명 / 스타일넘버 / 등록일(신상품 판정) /
  정상가 / 판매가(=온라인 1차세일가) / 쿠폰포함가 / 최종최저가 / 담당 MD / 상품 이미지 URL / 상품페이지 링크
  를 한 번에 뽑는다 (원천 musinsa.bizest.goods + partnerportal.brand + musinsa.itgg.goods_sale_price_changes).
  Use whenever the user asks 상품 정보 / 상품명 / 브랜드 / 스타일No / 등록일 / 판매가 / 세일가 / 쿠폰가 /
  상품 이미지(URL·썸네일·사진) / 상품 웹링크·판매링크·상품페이지 / online price of a goods_no(=UID)
  (단건·리스트·브랜드·상품명·MD 검색), or wants a goods_no ↔ 상품명/브랜드/가격/이미지/링크 매핑. 여러 장 상세 이미지는 bizest.goods_image.
---

# 무신사 상품 마스터 (+ 온라인 가격) — 조회 스킬

goods_no를 넣으면 **상품 마스터 속성 + 현재 온라인 판매가**를 한 번에 뽑는 스킬.
(2026-08 통합: 기존 `musinsa-goods-master` + `musinsa-online-price`를 1개로 합침 — **`판매가`는 이제 상품마스터의 bizest.goods.price가 아니라 온라인 1차세일가**로 반환.)

- 실행: Databricks MCP `mcp__databricks__execute_sql` (없으면 `ToolSearch "select:mcp__databricks__execute_sql"` 로 로드)
- 원천: `musinsa.bizest.goods`(상품마스터) + `musinsa.partnerportal.brand`(브랜드명) + `musinsa.itgg.goods_sale_price_changes`(온라인 가격, `channel_id=1`, 상품별 `created_at` 최신 1건 = 현재가)
- key = `goods_no`. 1행 = 1상품.

## 반환 컬럼

| 컬럼 | 원천 | 뜻 |
|---|---|---|
| `goods_no` | goods.goods_no | 상품번호 |
| `상품명` | goods.goods_nm | 상품명 |
| `브랜드명` | brand.brand_nm | 브랜드명(코드→명) |
| `brand_code` | goods.brand | 브랜드 코드 |
| `스타일넘버` | goods.style_no | 스타일 No |
| `등록일` | goods.reg_dm | 상품 등록일 → **신상품 판정 기준** |
| `정상가` | goods.normal_price | 세일 전 정상가 |
| `판매가` | **price_changes.sale_price** | **온라인 1차세일가** (브랜드/플랫폼 세일 적용가). 온라인 이력 없으면 bizest.goods.price→normal_price로 폴백 |
| `쿠폰포함가` | price_changes.coupon_dc_price | 쿠폰 할인까지 적용가 (없으면 판매가) |
| `최종최저가` | price_changes.final_price | 최저가도전·회원·적립 등 추가할인까지 반영 |
| `md_id` | goods.md_id | 담당 MD 계정(예: hong.gildong) |
| `image_url` | goods.img + img_update | 대표 이미지 URL = `https://image.msscdn.net`+img+`?t=`+img_update(캐시버전). HTTP 200 검증. img_yn='N'이면 무효 |
| `product_url` | goods.goods_no | 판매 웹링크 = `https://www.musinsa.com/products/{goods_no}` (goods_no가 곧 "UID") |

> `판매가`는 **온라인 실판매 1차세일가**다(구 버전의 bizest.goods.price 아님). 가격 캐스케이드: 정상가 →①브랜드/플랫폼세일=**판매가** →②쿠폰=쿠폰포함가 →③최저가도전=최종최저가. `sale_type='END'`면 판매가=정상가. (검증 goods 4841258: 정상가 66,500 · 판매가 39,900 · 쿠폰 33,920 · 최종 33,610)

## 표준 쿼리

`{{GOODS_FILTER}}` 부분만 상황에 맞게 바꿔 실행한다.

```sql
WITH bad_px AS (   -- 손상 방어: 원천에 '한 시각에 판매가가 2개 이상 충돌하는 (goods,시각)' 배치가 있음
                   -- (예 goods 5943429, 2026-08-08 14:56에 91·155·2385000… 13행). 최신이 이 배치면 엉뚱값 선택 → 제외.
  SELECT goods_id, created_at
  FROM musinsa.itgg.goods_sale_price_changes
  WHERE channel_id = 1
  GROUP BY goods_id, created_at HAVING COUNT(DISTINCT sale_price) > 1
),
price AS (
  SELECT p.goods_id, p.basic_price, p.sale_price, p.coupon_dc_price, p.final_price, p.sale_type,
         ROW_NUMBER() OVER (PARTITION BY p.goods_id ORDER BY p.created_at DESC) AS rn
  FROM musinsa.itgg.goods_sale_price_changes p
  LEFT ANTI JOIN bad_px b ON b.goods_id = p.goods_id AND b.created_at = p.created_at
  WHERE p.channel_id = 1                 -- MUSINSA 온라인
)
SELECT
    CAST(g.goods_no AS BIGINT)                                      AS goods_no,
    ANY_VALUE(g.goods_nm)                                          AS `상품명`,
    ANY_VALUE(b.brand_nm)                                          AS `브랜드명`,
    ANY_VALUE(g.brand)                                             AS brand_code,
    ANY_VALUE(g.style_no)                                          AS `스타일넘버`,
    MIN(CAST(g.reg_dm AS DATE))                                    AS `등록일`,
    CAST(ANY_VALUE(g.normal_price) AS BIGINT)                      AS `정상가`,
    -- 판매가 = 온라인 1차세일가 (없으면 bizest price → normal_price 폴백)
    CAST(COALESCE(ANY_VALUE(p.sale_price), ANY_VALUE(g.price), ANY_VALUE(g.normal_price)) AS BIGINT) AS `판매가`,
    CAST(COALESCE(ANY_VALUE(p.coupon_dc_price), ANY_VALUE(p.sale_price), ANY_VALUE(g.price)) AS BIGINT)                     AS `쿠폰포함가`,
    CAST(COALESCE(ANY_VALUE(p.final_price), ANY_VALUE(p.coupon_dc_price), ANY_VALUE(p.sale_price), ANY_VALUE(g.price)) AS BIGINT) AS `최종최저가`,
    ANY_VALUE(g.md_id)                                             AS md_id,
    -- 대표 이미지 URL (img_yn='Y'일 때 유효). ?t=img_update 는 캐시버전
    ANY_VALUE(CONCAT('https://image.msscdn.net', g.img, '?t=', DATE_FORMAT(g.img_update,'yyyyMMddHHmmss'))) AS image_url,
    -- 판매 웹링크 (goods_no = "UID")
    CONCAT('https://www.musinsa.com/products/', CAST(g.goods_no AS STRING))                                 AS product_url
FROM musinsa.bizest.goods g
LEFT JOIN musinsa.partnerportal.brand b ON b.brand = g.brand
LEFT JOIN (SELECT * FROM price WHERE rn = 1) p ON CAST(p.goods_id AS BIGINT) = CAST(g.goods_no AS BIGINT)
WHERE 1=1
  {{GOODS_FILTER}}
GROUP BY g.goods_no
ORDER BY goods_no;
```

### 필터 사용법 (`{{GOODS_FILTER}}`)
- **단일**: `AND CAST(g.goods_no AS BIGINT) = 4841258`
- **여러 상품**: `AND CAST(g.goods_no AS BIGINT) IN (4841258, 6594252, 6542750)`
- **브랜드 코드로**: `AND g.brand = 'brand_code'` (브랜드명만 알면 `partnerportal.brand`에서 코드 확인)
- **상품명 검색**: `AND g.goods_nm LIKE '%키워드%'` (LIMIT 권장)
- **담당 MD로**: `AND g.md_id = '<담당MD계정>'` (LIMIT 권장)

## 규칙
- 한글 컬럼 별칭은 **백틱** 필수(`` `상품명` ``). 아니면 `INVALID_IDENTIFIER`.
- `goods_no`/`goods_id`는 원천에서 문자열일 수 있어 `CAST(... AS BIGINT)`로 비교·조인·반환.
- **온라인 가격 이력이 없는 상품**은 `price`에 안 잡혀 `판매가`가 bizest.goods.price→normal_price로 폴백된다(정상 동작). `sale_type`이 필요하면 SELECT에 `ANY_VALUE(p.sale_type)` 추가.
- 브랜드가 `partnerportal.brand` 미매칭이면 `브랜드명` NULL → `brand_code`로 보완.
- 대량/전체 조회는 상품 수백만 건 → **반드시 `LIMIT` 또는 상위 필터(브랜드·MD)** 로 축소.
- **이미지/링크**: 사용자가 "UID"라 해도 내부적으로 `goods_no`로 처리. `image_url`은 `img_yn='N'`/`img` NULL이면 무효 → 필요시 SELECT에 `ANY_VALUE(g.img_yn)` 추가해 걸러 안내. `img`는 보통 500px 리스트 썸네일. **여러 장(리스트 L/상세 D, 대표 rep_yn)** 은 `musinsa.bizest.goods_image`(goods_no 키, `ORDER BY (rep_yn='Y') DESC, kind, seq` / 단 `?t=` 캐시버전 없음) 별도 조회.
- "오프라인에서 팔렸나/매장별 판매·재고"는 범위 밖 → `musinsa-offline-sales`(판매) / `musinsa-store-stock`(재고). 매장코드 매핑은 `musinsa-store-master`.
