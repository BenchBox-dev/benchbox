INSERT INTO DimSecurity (
  SK_SecurityID, Symbol, Issue, Status, Name, ExchangeID, SK_CompanyID, SharesOutstanding, FirstTrade,
  FirstTradeOnExchange, Dividend, IsCurrent, BatchID, EffectiveDate, EndDate
)
WITH sec AS (
  SELECT
    recdate AS effectivedate,
    trim(substring(value, 1, 15)) AS symbol,
    trim(substring(value, 16, 6)) AS issue,
    trim(substring(value, 22, 4)) AS status,
    trim(substring(value, 26, 70)) AS name,
    trim(substring(value, 96, 6)) AS exchangeid,
    CAST(trim(substring(value, 102, 13)) AS BIGINT) AS sharesoutstanding,
    CAST(strptime(substring(value, 115, 8), '%Y%m%d') AS DATE) AS firsttrade,
    CAST(strptime(substring(value, 123, 8), '%Y%m%d') AS DATE) AS firsttradeonexchange,
    CAST(trim(substring(value, 131, 12)) AS DECIMAL(10, 2)) AS dividend,
    trim(substring(value, 143, 60)) AS conameorcik
  FROM FinWire
  WHERE rectype = 'SEC'
),
sec_dated AS (
  SELECT
    *,
    CASE WHEN TRY_CAST(conameorcik AS BIGINT) IS NULL THEN 'NAME' ELSE 'CIK' END AS company_key_kind,
    CASE
      WHEN TRY_CAST(conameorcik AS BIGINT) IS NULL THEN conameorcik
      ELSE CAST(TRY_CAST(conameorcik AS BIGINT) AS VARCHAR)
    END AS company_key,
    coalesce(
      lead(effectivedate) OVER (PARTITION BY symbol ORDER BY effectivedate),
      DATE '9999-12-31'
    ) AS enddate
  FROM sec
),
company_keys AS (
  SELECT SK_CompanyID, 'CIK' AS company_key_kind, CAST(CompanyID AS VARCHAR) AS company_key, EffectiveDate, EndDate
  FROM DimCompany
  UNION ALL
  SELECT SK_CompanyID, 'NAME' AS company_key_kind, Name AS company_key, EffectiveDate, EndDate
  FROM DimCompany
),
split AS (
  SELECT
    s.symbol,
    s.issue,
    st.ST_NAME AS status,
    s.name,
    s.exchangeid,
    c.SK_CompanyID AS sk_companyid,
    s.sharesoutstanding,
    s.firsttrade,
    s.firsttradeonexchange,
    s.dividend,
    greatest(s.effectivedate, c.EffectiveDate) AS effectivedate,
    least(s.enddate, c.EndDate) AS enddate
  FROM sec_dated AS s
  JOIN company_keys AS c
    ON c.company_key_kind = s.company_key_kind
    AND c.company_key = s.company_key
    AND c.EffectiveDate < s.enddate
    AND c.EndDate > s.effectivedate
  JOIN StatusType AS st ON st.ST_ID = s.status
  WHERE s.effectivedate < s.enddate
)
SELECT
  row_number() OVER (ORDER BY effectivedate, symbol),
  symbol,
  issue,
  status,
  name,
  exchangeid,
  sk_companyid,
  sharesoutstanding,
  firsttrade,
  firsttradeonexchange,
  dividend,
  enddate = DATE '9999-12-31',
  1,
  effectivedate,
  enddate
FROM split
WHERE effectivedate < enddate
