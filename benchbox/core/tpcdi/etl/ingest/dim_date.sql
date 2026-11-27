INSERT INTO DimDate
SELECT *
FROM read_csv(
  $date_path,
  delim = '|',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'sk_dateid': 'BIGINT',
    'datevalue': 'DATE',
    'datedesc': 'VARCHAR',
    'calendaryearid': 'SMALLINT',
    'calendaryeardesc': 'VARCHAR',
    'calendarqtrid': 'INTEGER',
    'calendarqtrdesc': 'VARCHAR',
    'calendarmonthid': 'INTEGER',
    'calendarmonthdesc': 'VARCHAR',
    'calendarweekid': 'INTEGER',
    'calendarweekdesc': 'VARCHAR',
    'dayofweeknum': 'SMALLINT',
    'dayofweekdesc': 'VARCHAR',
    'fiscalyearid': 'SMALLINT',
    'fiscalyeardesc': 'VARCHAR',
    'fiscalqtrid': 'INTEGER',
    'fiscalqtrdesc': 'VARCHAR',
    'holidayflag': 'BOOLEAN'
  }
)
