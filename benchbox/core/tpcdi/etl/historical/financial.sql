INSERT INTO Financial (
  SK_CompanyID, FI_YEAR, FI_QTR, FI_QTR_START_DATE, FI_REVENUE, FI_NET_EARN, FI_BASIC_EPS, FI_DILUT_EPS,
  FI_MARGIN, FI_INVENTORY, FI_ASSETS, FI_LIABILITY, FI_OUT_BASIC, FI_OUT_DILUT
)
WITH fin AS (
  SELECT
    recdate,
    CAST(substring(value, 1, 4) AS SMALLINT) AS fi_year,
    CAST(substring(value, 5, 1) AS SMALLINT) AS fi_qtr,
    CAST(strptime(substring(value, 6, 8), '%Y%m%d') AS DATE) AS fi_qtr_start_date,
    CAST(trim(substring(value, 22, 17)) AS DECIMAL(15, 2)) AS fi_revenue,
    CAST(trim(substring(value, 39, 17)) AS DECIMAL(15, 2)) AS fi_net_earn,
    CAST(trim(substring(value, 56, 12)) AS DECIMAL(10, 2)) AS fi_basic_eps,
    CAST(trim(substring(value, 68, 12)) AS DECIMAL(10, 2)) AS fi_dilut_eps,
    CAST(trim(substring(value, 80, 12)) AS DECIMAL(10, 2)) AS fi_margin,
    CAST(trim(substring(value, 92, 17)) AS DECIMAL(15, 2)) AS fi_inventory,
    CAST(trim(substring(value, 109, 17)) AS DECIMAL(15, 2)) AS fi_assets,
    CAST(trim(substring(value, 126, 17)) AS DECIMAL(15, 2)) AS fi_liability,
    CAST(trim(substring(value, 143, 13)) AS BIGINT) AS fi_out_basic,
    CAST(trim(substring(value, 156, 13)) AS BIGINT) AS fi_out_dilut,
    trim(substring(value, 169, 60)) AS conameorcik
  FROM FinWire
  WHERE rectype = 'FIN'
),
fin_keyed AS (
  SELECT
    *,
    CASE WHEN TRY_CAST(conameorcik AS BIGINT) IS NULL THEN 'NAME' ELSE 'CIK' END AS company_key_kind,
    CASE
      WHEN TRY_CAST(conameorcik AS BIGINT) IS NULL THEN conameorcik
      ELSE CAST(TRY_CAST(conameorcik AS BIGINT) AS VARCHAR)
    END AS company_key
  FROM fin
),
company_keys AS (
  SELECT SK_CompanyID, 'CIK' AS company_key_kind, CAST(CompanyID AS VARCHAR) AS company_key, EffectiveDate, EndDate
  FROM DimCompany
  UNION ALL
  SELECT SK_CompanyID, 'NAME' AS company_key_kind, Name AS company_key, EffectiveDate, EndDate
  FROM DimCompany
)
SELECT
  c.SK_CompanyID,
  f.fi_year,
  f.fi_qtr,
  f.fi_qtr_start_date,
  f.fi_revenue,
  f.fi_net_earn,
  f.fi_basic_eps,
  f.fi_dilut_eps,
  f.fi_margin,
  f.fi_inventory,
  f.fi_assets,
  f.fi_liability,
  f.fi_out_basic,
  f.fi_out_dilut
FROM fin_keyed AS f
JOIN company_keys AS c
  ON c.company_key_kind = f.company_key_kind
  AND c.company_key = f.company_key
  AND f.recdate >= c.EffectiveDate
  AND f.recdate < c.EndDate
