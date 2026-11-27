INSERT INTO TaxRate
SELECT *
FROM read_csv(
  $tax_rate_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'tx_id': 'VARCHAR',
    'tx_name': 'VARCHAR',
    'tx_rate': 'DECIMAL(6,5)'
  }
)
