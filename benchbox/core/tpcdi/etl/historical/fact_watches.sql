INSERT INTO FactWatches (SK_CustomerID, SK_SecurityID, SK_DateID_DatePlaced, SK_DateID_DateRemoved, BatchID)
WITH watches AS (
  SELECT
    w_c_id AS customerid,
    w_s_symb AS symbol,
    CAST(min(CASE WHEN w_action = 'ACTV' THEN w_dts END) AS DATE) AS dateplaced,
    CAST(max(CASE WHEN w_action = 'CNCL' THEN w_dts END) AS DATE) AS dateremoved
  FROM WatchHistoryRaw
  WHERE batchid = 1
  GROUP BY w_c_id, w_s_symb
)
SELECT c.SK_CustomerID, s.SK_SecurityID, placed.SK_DateID, removed.SK_DateID, 1
FROM watches AS w
JOIN DimDate AS placed ON placed.DateValue = w.dateplaced
LEFT JOIN DimDate AS removed ON removed.DateValue = w.dateremoved
JOIN DimSecurity AS s
  ON s.Symbol = w.symbol
  AND w.dateplaced >= s.EffectiveDate
  AND w.dateplaced < s.EndDate
JOIN DimCustomer AS c
  ON c.CustomerID = w.customerid
  AND w.dateplaced >= c.EffectiveDate
  AND w.dateplaced < c.EndDate
