INSERT INTO FactCashBalances (SK_CustomerID, SK_AccountID, SK_DateID, Cash, BatchID)
WITH daily AS (
  SELECT accountid, CAST(ct_dts AS DATE) AS datevalue, sum(ct_amt) AS amount
  FROM CashTransactionRaw
  WHERE batchid = 1
  GROUP BY accountid, CAST(ct_dts AS DATE)
),
balances AS (
  SELECT accountid, datevalue, sum(amount) OVER (PARTITION BY accountid ORDER BY datevalue) AS cash
  FROM daily
)
SELECT a.SK_CustomerID, a.SK_AccountID, d.SK_DateID, b.cash, 1
FROM balances AS b
JOIN DimDate AS d ON d.DateValue = b.datevalue
JOIN DimAccount AS a
  ON a.AccountID = b.accountid
  AND b.datevalue >= a.EffectiveDate
  AND b.datevalue < a.EndDate
