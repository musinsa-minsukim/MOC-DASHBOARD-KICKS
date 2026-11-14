# 매장 코드 매핑표 — SELECTSHOP / KICKS / BEAUTY / OUTLET

2026-07 기준. 키는 **BIZEST `shop_no`**. ERP는 **`(플랜트, lgort)` 쌍 필수** (lgort는 플랜트 간 재사용됨: `3360` = 1000이면 무신사 스토어 성수, 1700이면 스타필드_고양).

## 소스 및 한계 (반드시 숙지)

| 소스 | 제공 | 상태 |
|---|---|---|
| `musinsa.stock.shop` | shop_no, shop_name, **shop_type** | 76개, max shop_no **99**, **2026-04-16 정지** |
| `musinsa.stock.shop_location` | shop_no → **erp_plant, erp_location_code** | 65개, **플랜트 1000·9000만 (1700 없음)**, 04-29 정지 |
| `team.commercepm.offline_shopno_storageid` | shop_no → **SCM storage_id** | 18개(수기), 신규매장 포함 |

⚠️ **3가지 함정**
1. **`shop_type`에 KICKS 없음** — 킥스 매장은 `UNKNOWN`으로 분류됨(홍대 79, 성수 89).
2. **shop 마스터 4월 정지** — 이후 오픈 매장 누락(고양 킥스 shop_no 136 미등록). 아래 표엔 수동 보강함.
3. **`shop_location`에 플랜트 1700 없음** — 1700 lgort는 `pbo.moms.iif_sap_sto`의 매장명으로 추정해야 함(아래 `*` 표시).

## 매핑표

`1000` / `9000` / `SCM` = 코드 기반(정확). `1700*` = 매장명 기반 추정(검증 권장).

| shop_no | 매장 | type | ERP 1000 | ERP 1700* | ERP 9000 | SCM storage_id |
|---|---|---|---|---|---|---|
| 7 | 무신사 스토어 대구 | SELECTSHOP | 3040 | 3040 | 3040 | 51 |
| 8 | 무신사 스토어 홍대 | SELECTSHOP | 3050 | 3045 | 3050 | 52 |
| 25 | 무신사 스토어 성수 | SELECTSHOP | 3360 | 3042 | 3360 | 53 |
| 55 | 무신사 스토어 강남 | SELECTSHOP | 3160 | 3039 | 3160 | 105 |
| 67 | 무신사 걸즈 타임스퀘어 영등포점 | SELECTSHOP | 3520 | 3046 | 3520 | 130 |
| 68 | 무신사 메가스토어 용산 | SELECTSHOP | 3530 | 3043 | 3530 | 131 |
| 82 | 무신사 스토어 롯데백화점 잠실점 | SELECTSHOP | 3590 | 3044 | 3590 | 137 |
| 83 | 무신사 스토어 명동 | SELECTSHOP | 3580 | 3041 | 3580 | 138 |
| 88 | 무신사 메가스토어 성수 | SELECTSHOP | 3532 | 3049 | 3532 | 146 |
| 91 | 무신사 백&캡클럽 서울숲 | SELECTSHOP | 3650 | 3050 | 3650 | 148 |
| 98 | 무신사 스토어 AK플라자 수원점 | SELECTSHOP | 3170 | 3390?⚠️ | 3170 | 154 |
| 79 | 무신사 킥스 홍대 | KICKS(=UNKNOWN) | 3055 | 3038 | 3055 | 135 |
| 89 | 무신사 킥스 성수 | KICKS(=UNKNOWN) | 3057 | 3048 | 3057 | 147 |
| 136 | 무신사 킥스 스타필드 고양점 | KICKS(마스터 미등록) | 3058 | 3053 | — | 197 |
| 19 | 무신사 뷰티 스페이스 1 | BEAUTY | 3134 | — | 3134 | — |
| 86 | 무신사 아울렛&유즈드 롯데몰은평 | OUTLET | 3600 | 3047 | 3600 | 145 |

⚠️ shop_no 98의 `1700-3390`은 플랜트 1000에선 `스탠다드_롯데_잠실`이라 충돌 소지 — 사용 전 확인.
(참고: 99 오드타입&위찌 서울숲 BEAUTY / 4 OUTLET(무명)은 `use_yn=0` 또는 데이터 불량이라 제외.)

## 재생성 쿼리 (shop_no 기반)

```sql
WITH sh AS (
  SELECT shop_no, shop_name, shop_type, use_yn FROM (
    SELECT shop_no, shop_name, shop_type, use_yn,
      ROW_NUMBER() OVER (PARTITION BY shop_no ORDER BY (Op='delete'), tr_ts DESC) rn
    FROM musinsa.stock.shop) WHERE rn=1
),
loc AS (
  SELECT DISTINCT shop_no, erp_plant, erp_location_code
  FROM musinsa.stock.shop_location WHERE COALESCE(Op,'')<>'delete'
),
scm AS (SELECT shop_no, storage_id FROM team.commercepm.offline_shopno_storageid)
SELECT sh.shop_no, sh.shop_name, sh.shop_type, sh.use_yn,
  MAX(CASE WHEN loc.erp_plant='1000' THEN loc.erp_location_code END) AS erp_1000,
  MAX(CASE WHEN loc.erp_plant='9000' THEN loc.erp_location_code END) AS erp_9000,
  MAX(scm.storage_id) AS scm_id
FROM sh
LEFT JOIN loc ON loc.shop_no = sh.shop_no
LEFT JOIN scm ON scm.shop_no = sh.shop_no
WHERE sh.shop_type IN ('SELECTSHOP','BEAUTY','OUTLET') OR sh.shop_name LIKE '%킥스%'
GROUP BY sh.shop_no, sh.shop_name, sh.shop_type, sh.use_yn
ORDER BY sh.shop_type, sh.shop_no;
```

1700 lgort 확인:
```sql
SELECT WERKS, GR_LGORT, GR_LGOBE, COUNT(*) FROM pbo.moms.iif_sap_sto
WHERE WERKS='1700' AND GR_LGORT LIKE '3%' GROUP BY 1,2,3 ORDER BY 2;
```

## 사용 예 (통합 쿼리 파라미터)

무신사 킥스 스타필드 고양점(shop_no 136):
- `{{ERP_PAIRS}}` = `'1000-3058','1700-3053'`
- `{{ERP_LGORTS}}` = `'3058','3053'`
- `{{SCM_STORAGE}}` = `197`
