CREATE OR REPLACE TABLE TradeRaw AS
SELECT
  * EXCLUDE (filename),
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $trade_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'cdc_flag': 'VARCHAR',
    'cdc_dsn': 'BIGINT',
    't_id': 'BIGINT',
    't_dts': 'TIMESTAMP',
    't_st_id': 'VARCHAR',
    't_tt_id': 'VARCHAR',
    't_is_cash': 'SMALLINT',
    't_s_symb': 'VARCHAR',
    'quantity': 'INTEGER',
    'bidprice': 'DECIMAL(8,2)',
    't_ca_id': 'BIGINT',
    'executedby': 'VARCHAR',
    'tradeprice': 'DECIMAL(8,2)',
    'fee': 'DECIMAL(10,2)',
    'commission': 'DECIMAL(10,2)',
    'tax': 'DECIMAL(10,2)'
  }
)
