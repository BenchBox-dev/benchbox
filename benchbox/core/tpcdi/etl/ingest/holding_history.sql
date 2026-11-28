CREATE OR REPLACE TABLE HoldingHistoryRaw AS
SELECT
  * EXCLUDE (filename),
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $holding_history_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'hh_h_t_id': 'BIGINT',
    'hh_t_id': 'BIGINT',
    'hh_before_qty': 'INTEGER',
    'hh_after_qty': 'INTEGER'
  }
)
