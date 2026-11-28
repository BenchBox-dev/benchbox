INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'DimTrade', 'Invalid trade commission', 'Alert', concat('T_ID = ', t_id, ', T_COMM = ', commission)
FROM TradeRaw
WHERE batchid = $batch_id
  AND commission IS NOT NULL
  AND commission > tradeprice * quantity;

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'DimTrade', 'Invalid trade fee', 'Alert', concat('T_ID = ', t_id, ', T_CHRG = ', fee)
FROM TradeRaw
WHERE batchid = $batch_id
  AND fee IS NOT NULL
  AND fee > tradeprice * quantity;

CREATE TEMPORARY TABLE trade_changes AS
WITH latest AS (
  SELECT *
  FROM TradeRaw
  WHERE batchid = $batch_id
  QUALIFY row_number() OVER (PARTITION BY t_id ORDER BY cdc_dsn DESC) = 1
),
stamps AS (
  SELECT
    t_id,
    min(CASE WHEN cdc_flag = 'I' THEN t_dts END) AS create_ts,
    arg_max(t_dts, cdc_dsn) FILTER (WHERE t_st_id IN ('CMPT', 'CNCL')) AS close_ts
  FROM TradeRaw
  WHERE batchid = $batch_id
  GROUP BY t_id
)
SELECT l.*, s.create_ts, s.close_ts
FROM latest AS l
JOIN stamps AS s ON s.t_id = l.t_id;

UPDATE DimTrade AS t
SET
  Status = st.ST_NAME,
  Type = tt.TT_NAME,
  CashFlag = c.t_is_cash = 1,
  Quantity = c.quantity,
  BidPrice = c.bidprice,
  ExecutedBy = c.executedby,
  TradePrice = c.tradeprice,
  Fee = c.fee,
  Commission = c.commission,
  Tax = c.tax,
  SK_CloseDateID = coalesce(xd.SK_DateID, t.SK_CloseDateID),
  SK_CloseTimeID = coalesce(xt.SK_TimeID, t.SK_CloseTimeID),
  BatchID = $batch_id
FROM trade_changes AS c
JOIN StatusType AS st ON st.ST_ID = c.t_st_id
JOIN TradeType AS tt ON tt.TT_ID = c.t_tt_id
LEFT JOIN DimDate AS xd ON xd.DateValue = CAST(c.close_ts AS DATE)
LEFT JOIN DimTime AS xt ON xt.TimeValue = CAST(c.close_ts AS TIME)
WHERE t.TradeID = c.t_id;

INSERT INTO DimTrade (
  TradeID, SK_BrokerID, SK_CreateDateID, SK_CreateTimeID, SK_CloseDateID, SK_CloseTimeID, Status, Type, CashFlag,
  SK_SecurityID, SK_CompanyID, Quantity, BidPrice, SK_CustomerID, SK_AccountID, ExecutedBy, TradePrice, Fee,
  Commission, Tax, BatchID
)
SELECT
  c.t_id,
  a.SK_BrokerID,
  cd.SK_DateID,
  ct.SK_TimeID,
  xd.SK_DateID,
  xt.SK_TimeID,
  st.ST_NAME,
  tt.TT_NAME,
  c.t_is_cash = 1,
  s.SK_SecurityID,
  s.SK_CompanyID,
  c.quantity,
  c.bidprice,
  a.SK_CustomerID,
  a.SK_AccountID,
  c.executedby,
  c.tradeprice,
  c.fee,
  c.commission,
  c.tax,
  $batch_id
FROM trade_changes AS c
JOIN StatusType AS st ON st.ST_ID = c.t_st_id
JOIN TradeType AS tt ON tt.TT_ID = c.t_tt_id
JOIN DimDate AS cd ON cd.DateValue = CAST(c.create_ts AS DATE)
JOIN DimTime AS ct ON ct.TimeValue = CAST(c.create_ts AS TIME)
LEFT JOIN DimDate AS xd ON xd.DateValue = CAST(c.close_ts AS DATE)
LEFT JOIN DimTime AS xt ON xt.TimeValue = CAST(c.close_ts AS TIME)
JOIN DimSecurity AS s ON s.Symbol = c.t_s_symb AND s.IsCurrent
JOIN DimAccount AS a ON a.AccountID = c.t_ca_id AND a.IsCurrent
WHERE c.create_ts IS NOT NULL
  AND c.t_id NOT IN (SELECT TradeID FROM DimTrade);

DROP TABLE trade_changes
