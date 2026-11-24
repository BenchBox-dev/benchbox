CREATE OR REPLACE TABLE WatchHistoryRaw AS
SELECT
  * EXCLUDE (filename),
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $watch_history_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'w_c_id': 'BIGINT',
    'w_s_symb': 'VARCHAR',
    'w_dts': 'TIMESTAMP',
    'w_action': 'VARCHAR'
  }
)
