INSERT INTO FinWire
SELECT
  substring(line, 16, 3) AS rectype,
  CAST(strptime(substring(line, 1, 8), '%Y%m%d') AS DATE) AS recdate,
  substring(line, 19) AS value
FROM read_csv(
  $finwire_path,
  delim = chr(31),
  header = false,
  quote = '',
  escape = '',
  columns = {
    'line': 'VARCHAR'
  }
)
