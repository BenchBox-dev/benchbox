CREATE OR REPLACE TABLE TradeHistoryRaw AS
SELECT
  * EXCLUDE (filename),
  try_cast(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\]', 1) AS INTEGER) AS batchid
FROM read_csv(
  $trade_history_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'tradeid': 'BIGINT',
    'th_dts': 'TIMESTAMP',
    'status': 'VARCHAR'
  }
)
