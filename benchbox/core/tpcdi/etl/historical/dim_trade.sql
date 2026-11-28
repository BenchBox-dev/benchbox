INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, 1, 'DimTrade', 'Invalid trade commission', 'Alert', concat('T_ID = ', t_id, ', T_COMM = ', commission)
FROM TradeRaw
WHERE batchid = 1
  AND commission IS NOT NULL
  AND commission > tradeprice * quantity;

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, 1, 'DimTrade', 'Invalid trade fee', 'Alert', concat('T_ID = ', t_id, ', T_CHRG = ', fee)
FROM TradeRaw
WHERE batchid = 1
  AND fee IS NOT NULL
  AND fee > tradeprice * quantity;

INSERT INTO DimTrade (
  TradeID, SK_BrokerID, SK_CreateDateID, SK_CreateTimeID, SK_CloseDateID, SK_CloseTimeID, Status, Type, CashFlag,
  SK_SecurityID, SK_CompanyID, Quantity, BidPrice, SK_CustomerID, SK_AccountID, ExecutedBy, TradePrice, Fee,
  Commission, Tax, BatchID
)
WITH history AS (
  SELECT
    tradeid,
    min(th_dts) AS create_ts,
    max(CASE WHEN status IN ('CMPT', 'CNCL') THEN th_dts END) AS close_ts
  FROM TradeHistoryRaw
  WHERE batchid = 1
  GROUP BY tradeid
),
trades AS (
  SELECT t.*, h.create_ts, h.close_ts
  FROM TradeRaw AS t
  JOIN history AS h ON h.tradeid = t.t_id
  WHERE t.batchid = 1
)
SELECT
  t.t_id,
  a.SK_BrokerID,
  cd.SK_DateID,
  ct.SK_TimeID,
  xd.SK_DateID,
  xt.SK_TimeID,
  st.ST_NAME,
  tt.TT_NAME,
  t.t_is_cash = 1,
  s.SK_SecurityID,
  s.SK_CompanyID,
  t.quantity,
  t.bidprice,
  a.SK_CustomerID,
  a.SK_AccountID,
  t.executedby,
  t.tradeprice,
  t.fee,
  t.commission,
  t.tax,
  1
FROM trades AS t
JOIN StatusType AS st ON st.ST_ID = t.t_st_id
JOIN TradeType AS tt ON tt.TT_ID = t.t_tt_id
JOIN DimDate AS cd ON cd.DateValue = CAST(t.create_ts AS DATE)
JOIN DimTime AS ct ON ct.TimeValue = CAST(t.create_ts AS TIME)
LEFT JOIN DimDate AS xd ON xd.DateValue = CAST(t.close_ts AS DATE)
LEFT JOIN DimTime AS xt ON xt.TimeValue = CAST(t.close_ts AS TIME)
JOIN DimSecurity AS s
  ON s.Symbol = t.t_s_symb
  AND CAST(t.create_ts AS DATE) >= s.EffectiveDate
  AND CAST(t.create_ts AS DATE) < s.EndDate
JOIN DimAccount AS a
  ON a.AccountID = t.t_ca_id
  AND CAST(t.create_ts AS DATE) >= a.EffectiveDate
  AND CAST(t.create_ts AS DATE) < a.EndDate
