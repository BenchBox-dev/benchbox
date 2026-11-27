CREATE OR REPLACE TABLE CashTransactionRaw AS
SELECT
  * EXCLUDE (filename),
  CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
FROM read_csv(
  $cash_transaction_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'accountid': 'BIGINT',
    'ct_dts': 'TIMESTAMP',
    'ct_amt': 'DECIMAL(15,2)',
    'ct_name': 'VARCHAR'
  }
)
