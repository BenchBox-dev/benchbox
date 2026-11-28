CREATE OR REPLACE TABLE AccountRaw AS
SELECT
  * EXCLUDE (filename),
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $account_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'cdc_flag': 'VARCHAR',
    'cdc_dsn': 'BIGINT',
    'accountid': 'BIGINT',
    'brokerid': 'BIGINT',
    'customerid': 'BIGINT',
    'accountdesc': 'VARCHAR',
    'taxstatus': 'SMALLINT',
    'status': 'VARCHAR'
  }
)
