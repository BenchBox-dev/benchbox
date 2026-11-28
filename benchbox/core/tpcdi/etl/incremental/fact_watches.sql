CREATE TEMPORARY TABLE watch_changes AS
SELECT
  w_c_id AS customerid,
  w_s_symb AS symbol,
  CAST(min(CASE WHEN w_action = 'ACTV' THEN w_dts END) AS DATE) AS dateplaced,
  CAST(max(CASE WHEN w_action = 'CNCL' THEN w_dts END) AS DATE) AS dateremoved
FROM WatchHistoryRaw
WHERE batchid = $batch_id
GROUP BY w_c_id, w_s_symb;

INSERT INTO FactWatches (SK_CustomerID, SK_SecurityID, SK_DateID_DatePlaced, SK_DateID_DateRemoved, BatchID)
SELECT c.SK_CustomerID, s.SK_SecurityID, placed.SK_DateID, removed.SK_DateID, $batch_id
FROM watch_changes AS w
JOIN DimDate AS placed ON placed.DateValue = w.dateplaced
LEFT JOIN DimDate AS removed ON removed.DateValue = w.dateremoved
JOIN DimSecurity AS s ON s.Symbol = w.symbol AND s.IsCurrent
JOIN DimCustomer AS c ON c.CustomerID = w.customerid AND c.IsCurrent
WHERE w.dateplaced IS NOT NULL;

UPDATE FactWatches AS f
SET SK_DateID_DateRemoved = removed.SK_DateID, BatchID = $batch_id
FROM watch_changes AS w
JOIN DimDate AS removed ON removed.DateValue = w.dateremoved
JOIN DimCustomer AS c ON c.CustomerID = w.customerid
JOIN DimSecurity AS s ON s.Symbol = w.symbol
WHERE w.dateplaced IS NULL
  AND f.SK_CustomerID = c.SK_CustomerID
  AND f.SK_SecurityID = s.SK_SecurityID
  AND f.SK_DateID_DateRemoved IS NULL;

DROP TABLE watch_changes
