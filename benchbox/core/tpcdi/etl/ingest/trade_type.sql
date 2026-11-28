INSERT INTO TradeType
SELECT *
FROM read_csv(
  $trade_type_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'tt_id': 'VARCHAR',
    'tt_name': 'VARCHAR',
    'tt_is_sell': 'SMALLINT',
    'tt_is_mrkt': 'SMALLINT'
  }
)
