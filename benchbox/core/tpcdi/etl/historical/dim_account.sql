INSERT INTO DimAccount (
  SK_AccountID, AccountID, SK_BrokerID, SK_CustomerID, Status, AccountDesc, TaxStatus, IsCurrent, BatchID,
  EffectiveDate, EndDate
)
WITH account AS (
  SELECT
    accountid,
    customerid,
    CASE WHEN actiontype = 'CLOSEACCT' THEN 'INAC' ELSE 'ACTV' END AS status_id,
    last_value(accountdesc IGNORE NULLS) OVER w AS accountdesc,
    last_value(taxstatus IGNORE NULLS) OVER w AS taxstatus,
    last_value(brokerid IGNORE NULLS) OVER w AS brokerid,
    CAST(update_ts AS DATE) AS effectivedate,
    coalesce(
      lead(CAST(update_ts AS DATE)) OVER (PARTITION BY accountid ORDER BY update_ts),
      DATE '9999-12-31'
    ) AS enddate
  FROM CustomerMgmtRaw
  WHERE actiontype IN ('NEW', 'ADDACCT', 'UPDACCT', 'CLOSEACCT')
  WINDOW w AS (PARTITION BY accountid ORDER BY update_ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
split AS (
  SELECT
    a.accountid,
    a.brokerid,
    c.SK_CustomerID AS sk_customerid,
    a.status_id,
    a.accountdesc,
    a.taxstatus,
    greatest(a.effectivedate, c.EffectiveDate) AS effectivedate,
    least(a.enddate, c.EndDate) AS enddate
  FROM account AS a
  JOIN DimCustomer AS c
    ON c.CustomerID = a.customerid
    AND c.EffectiveDate < a.enddate
    AND c.EndDate > a.effectivedate
  WHERE a.effectivedate < a.enddate
)
SELECT
  CAST(strftime(s.effectivedate, '%Y%m%d') || CAST(s.accountid AS VARCHAR) AS BIGINT),
  s.accountid,
  b.SK_BrokerID,
  s.sk_customerid,
  st.ST_NAME,
  s.accountdesc,
  s.taxstatus,
  s.enddate = DATE '9999-12-31',
  1,
  s.effectivedate,
  s.enddate
FROM split AS s
JOIN DimBroker AS b ON b.BrokerID = s.brokerid
JOIN StatusType AS st ON st.ST_ID = s.status_id
WHERE s.effectivedate < s.enddate
