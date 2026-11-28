INSERT INTO Industry
SELECT *
FROM read_csv(
  $industry_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'in_id': 'VARCHAR',
    'in_name': 'VARCHAR',
    'in_sc_id': 'VARCHAR'
  }
)
