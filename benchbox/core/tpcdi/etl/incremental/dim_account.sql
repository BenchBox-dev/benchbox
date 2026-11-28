CREATE TEMPORARY TABLE account_changes AS
WITH batch AS (
  SELECT batchdate FROM BatchDate WHERE batchid = $batch_id
),
latest AS (
  SELECT *
  FROM AccountRaw
  WHERE batchid = $batch_id
  QUALIFY row_number() OVER (PARTITION BY accountid ORDER BY cdc_dsn DESC) = 1
),
customer_moves AS (
  SELECT
    cur.CustomerID AS customerid,
    cur.SK_CustomerID AS new_sk,
    prev.SK_CustomerID AS old_sk,
    prev.Status AS old_status
  FROM DimCustomer AS cur
  JOIN batch AS b ON true
  JOIN DimCustomer AS prev
    ON prev.CustomerID = cur.CustomerID
    AND NOT prev.IsCurrent
    AND prev.EndDate = b.batchdate
  WHERE cur.IsCurrent
    AND cur.BatchID = $batch_id
),
account_update AS (
  SELECT
    a.accountid,
    br.SK_BrokerID AS sk_brokerid,
    c.SK_CustomerID AS sk_customerid,
    st.ST_NAME AS status,
    a.accountdesc,
    a.taxstatus
  FROM latest AS a
  JOIN DimBroker AS br ON br.BrokerID = a.brokerid AND br.IsCurrent
  JOIN DimCustomer AS c ON c.CustomerID = a.customerid AND c.IsCurrent
  JOIN StatusType AS st ON st.ST_ID = a.status
),
cascaded AS (
  SELECT
    a.AccountID AS accountid,
    a.SK_BrokerID AS sk_brokerid,
    m.new_sk AS sk_customerid,
    a.Status AS status,
    a.AccountDesc AS accountdesc,
    a.TaxStatus AS taxstatus
  FROM DimAccount AS a
  JOIN customer_moves AS m ON m.old_sk = a.SK_CustomerID
  WHERE a.IsCurrent
    AND a.AccountID NOT IN (SELECT accountid FROM latest)
),
combined AS (
  SELECT * FROM account_update
  UNION ALL
  SELECT * FROM cascaded
)
SELECT
  x.accountid,
  x.sk_brokerid,
  x.sk_customerid,
  CASE
    WHEN c.Status = inactive.ST_NAME AND m.old_status IS NOT NULL AND m.old_status <> inactive.ST_NAME
      THEN inactive.ST_NAME
    ELSE x.status
  END AS status,
  x.accountdesc,
  x.taxstatus
FROM combined AS x
JOIN DimCustomer AS c ON c.SK_CustomerID = x.sk_customerid
LEFT JOIN customer_moves AS m ON m.customerid = c.CustomerID
JOIN StatusType AS inactive ON inactive.ST_ID = 'INAC';

UPDATE DimAccount
SET IsCurrent = false, EndDate = (SELECT batchdate FROM BatchDate WHERE batchid = $batch_id)
WHERE IsCurrent AND AccountID IN (SELECT accountid FROM account_changes);

INSERT INTO DimAccount (
  SK_AccountID, AccountID, SK_BrokerID, SK_CustomerID, Status, AccountDesc, TaxStatus, IsCurrent, BatchID,
  EffectiveDate, EndDate
)
SELECT
  CAST(strftime(b.batchdate, '%Y%m%d') || CAST(a.accountid AS VARCHAR) AS BIGINT),
  a.accountid,
  a.sk_brokerid,
  a.sk_customerid,
  a.status,
  a.accountdesc,
  a.taxstatus,
  true,
  $batch_id,
  b.batchdate,
  DATE '9999-12-31'
FROM account_changes AS a
JOIN BatchDate AS b ON b.batchid = $batch_id;

DROP TABLE account_changes
