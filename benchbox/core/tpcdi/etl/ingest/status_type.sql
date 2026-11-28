INSERT INTO StatusType
SELECT *
FROM read_csv(
  $status_type_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'st_id': 'VARCHAR',
    'st_name': 'VARCHAR'
  }
)
