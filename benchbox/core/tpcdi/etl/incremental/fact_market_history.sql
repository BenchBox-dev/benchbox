CREATE TEMPORARY TABLE market_history_staging AS
WITH batch AS (
  SELECT dm_date, dm_s_symb, dm_close, dm_high, dm_low, dm_vol
  FROM DailyMarketRaw
  WHERE batchid = $batch_id
),
history AS (
  SELECT s.Symbol AS dm_s_symb, d.DateValue AS dm_date, f.DayHigh AS dm_high, f.DayLow AS dm_low
  FROM FactMarketHistory AS f
  JOIN DimSecurity AS s ON s.SK_SecurityID = f.SK_SecurityID
  JOIN DimDate AS d ON d.SK_DateID = f.SK_DateID
  WHERE s.Symbol IN (SELECT dm_s_symb FROM batch)
    AND d.DateValue > (SELECT min(dm_date) FROM batch) - INTERVAL 1 YEAR
),
combined AS (
  SELECT dm_date, dm_s_symb, dm_close, dm_high, dm_low, dm_vol, true AS in_batch FROM batch
  UNION ALL
  SELECT dm_date, dm_s_symb, NULL, dm_high, dm_low, NULL, false FROM history
),
windowed AS (
  SELECT
    dm_date,
    dm_s_symb,
    dm_close,
    dm_high,
    dm_low,
    dm_vol,
    in_batch,
    max(dm_high) OVER fifty_two_weeks AS fiftytwoweekhigh,
    arg_max(dm_date, CAST(dm_high * 100 AS BIGINT) * 100000000 - date_diff('day', DATE '1800-01-01', dm_date))
      OVER fifty_two_weeks AS fiftytwoweekhighdate,
    min(dm_low) OVER fifty_two_weeks AS fiftytwoweeklow,
    arg_min(dm_date, CAST(dm_low * 100 AS BIGINT) * 100000000 + date_diff('day', DATE '1800-01-01', dm_date))
      OVER fifty_two_weeks AS fiftytwoweeklowdate
  FROM combined
  WINDOW fifty_two_weeks AS (
    PARTITION BY dm_s_symb
    ORDER BY dm_date
    RANGE BETWEEN INTERVAL '1 year -1 day' PRECEDING AND CURRENT ROW
  )
),
priced AS (
  SELECT
    m.*,
    s.SK_SecurityID,
    s.SK_CompanyID,
    s.Dividend,
    c.CompanyID,
    date_trunc('quarter', m.dm_date) AS quarter_start
  FROM windowed AS m
  JOIN DimSecurity AS s ON s.Symbol = m.dm_s_symb AND s.IsCurrent
  JOIN DimCompany AS c ON c.SK_CompanyID = s.SK_CompanyID
  WHERE m.in_batch
),
quarters AS (
  SELECT DISTINCT CompanyID, quarter_start FROM priced
),
earnings AS (
  SELECT q.CompanyID, q.quarter_start, sum(f.FI_BASIC_EPS) AS eps
  FROM quarters AS q
  JOIN DimCompany AS c ON c.CompanyID = q.CompanyID
  JOIN Financial AS f
    ON f.SK_CompanyID = c.SK_CompanyID
    AND f.FI_QTR_START_DATE >= q.quarter_start - INTERVAL 1 YEAR
    AND f.FI_QTR_START_DATE < q.quarter_start
  GROUP BY q.CompanyID, q.quarter_start
)
SELECT p.*, e.eps
FROM priced AS p
LEFT JOIN earnings AS e ON e.CompanyID = p.CompanyID AND e.quarter_start = p.quarter_start;

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'FactMarketHistory', 'No earnings for company', 'Alert', concat('DM_S_SYMB = ', dm_s_symb)
FROM market_history_staging
WHERE eps IS NULL;

INSERT INTO FactMarketHistory (
  SK_SecurityID, SK_CompanyID, SK_DateID, PERatio, Yield, FiftyTwoWeekHigh, SK_FiftyTwoWeekHighDate,
  FiftyTwoWeekLow, SK_FiftyTwoWeekLowDate, ClosePrice, DayHigh, DayLow, Volume, BatchID
)
SELECT
  m.SK_SecurityID,
  m.SK_CompanyID,
  d.SK_DateID,
  CAST(m.dm_close / nullif(m.eps, 0) AS DECIMAL(10, 2)),
  CAST(m.Dividend * 100 / m.dm_close AS DECIMAL(5, 2)),
  m.fiftytwoweekhigh,
  hd.SK_DateID,
  m.fiftytwoweeklow,
  ld.SK_DateID,
  m.dm_close,
  m.dm_high,
  m.dm_low,
  m.dm_vol,
  $batch_id
FROM market_history_staging AS m
JOIN DimDate AS d ON d.DateValue = m.dm_date
JOIN DimDate AS hd ON hd.DateValue = m.fiftytwoweekhighdate
JOIN DimDate AS ld ON ld.DateValue = m.fiftytwoweeklowdate;

DROP TABLE market_history_staging
