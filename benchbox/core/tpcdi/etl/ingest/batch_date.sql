INSERT INTO BatchDate (batchdate, batchid)
SELECT
  batchdate,
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $batch_date_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'batchdate': 'DATE'
  }
)
