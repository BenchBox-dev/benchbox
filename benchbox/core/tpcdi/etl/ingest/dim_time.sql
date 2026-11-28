INSERT INTO DimTime
SELECT *
FROM read_csv(
  $time_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'sk_timeid': 'BIGINT',
    'timevalue': 'TIME',
    'hourid': 'SMALLINT',
    'hourdesc': 'VARCHAR',
    'minuteid': 'SMALLINT',
    'minutedesc': 'VARCHAR',
    'secondid': 'SMALLINT',
    'seconddesc': 'VARCHAR',
    'markethoursflag': 'BOOLEAN',
    'officehoursflag': 'BOOLEAN'
  }
)
