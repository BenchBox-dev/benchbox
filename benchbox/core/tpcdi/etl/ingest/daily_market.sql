CREATE OR REPLACE TABLE DailyMarketRaw AS
SELECT
  * EXCLUDE (filename),
  try_cast(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\]', 1) AS INTEGER) AS batchid
FROM read_csv(
  $daily_market_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'dm_date': 'DATE',
    'dm_s_symb': 'VARCHAR',
    'dm_close': 'DECIMAL(8,2)',
    'dm_high': 'DECIMAL(8,2)',
    'dm_low': 'DECIMAL(8,2)',
    'dm_vol': 'BIGINT'
  }
)
