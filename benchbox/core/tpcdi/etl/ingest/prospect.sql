CREATE OR REPLACE TABLE ProspectRaw AS
SELECT
  * EXCLUDE (filename),
  try_cast(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\]', 1) AS INTEGER) AS batchid
FROM read_csv(
  $prospect_path,
  delim = ',',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  filename = true,
  columns = {
    'agencyid': 'VARCHAR',
    'lastname': 'VARCHAR',
    'firstname': 'VARCHAR',
    'middleinitial': 'VARCHAR',
    'gender': 'VARCHAR',
    'addressline1': 'VARCHAR',
    'addressline2': 'VARCHAR',
    'postalcode': 'VARCHAR',
    'city': 'VARCHAR',
    'state': 'VARCHAR',
    'country': 'VARCHAR',
    'phone': 'VARCHAR',
    'income': 'BIGINT',
    'numbercars': 'SMALLINT',
    'numberchildren': 'SMALLINT',
    'maritalstatus': 'VARCHAR',
    'age': 'SMALLINT',
    'creditrating': 'SMALLINT',
    'ownorrentflag': 'VARCHAR',
    'employer': 'VARCHAR',
    'numbercreditcards': 'SMALLINT',
    'networth': 'BIGINT'
  }
)
