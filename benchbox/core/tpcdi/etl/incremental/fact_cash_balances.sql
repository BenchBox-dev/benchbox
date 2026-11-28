INSERT INTO FactCashBalances (SK_CustomerID, SK_AccountID, SK_DateID, Cash, BatchID)
WITH daily AS (
  SELECT accountid, sum(ct_amt) AS amount
  FROM CashTransactionRaw
  WHERE batchid = $batch_id
  GROUP BY accountid
),
prior AS (
  SELECT a.AccountID AS accountid, arg_max(f.Cash, f.SK_DateID) AS cash
  FROM FactCashBalances AS f
  JOIN DimAccount AS a ON a.SK_AccountID = f.SK_AccountID
  WHERE a.AccountID IN (SELECT accountid FROM daily)
  GROUP BY a.AccountID
)
SELECT cur.SK_CustomerID, cur.SK_AccountID, d.SK_DateID, coalesce(p.cash, 0) + daily.amount, $batch_id
FROM daily
JOIN DimAccount AS cur ON cur.AccountID = daily.accountid AND cur.IsCurrent
JOIN BatchDate AS b ON b.batchid = $batch_id
JOIN DimDate AS d ON d.DateValue = b.batchdate
LEFT JOIN prior AS p ON p.accountid = daily.accountid
