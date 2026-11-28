INSERT INTO FactHoldings (
  TradeID, CurrentTradeID, SK_CustomerID, SK_AccountID, SK_SecurityID, SK_CompanyID, SK_DateID, SK_TimeID,
  CurrentPrice, CurrentHolding, BatchID
)
SELECT
  h.hh_h_t_id,
  h.hh_t_id,
  t.SK_CustomerID,
  t.SK_AccountID,
  t.SK_SecurityID,
  t.SK_CompanyID,
  t.SK_CloseDateID,
  t.SK_CloseTimeID,
  t.TradePrice,
  h.hh_after_qty,
  $batch_id
FROM HoldingHistoryRaw AS h
JOIN DimTrade AS t ON t.TradeID = h.hh_t_id
WHERE h.batchid = $batch_id
