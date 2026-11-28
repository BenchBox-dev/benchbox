INSERT INTO DimBroker
SELECT
  employeeid AS sk_brokerid,
  employeeid AS brokerid,
  managerid,
  employeefirstname AS firstname,
  employeelastname AS lastname,
  employeemi AS middleinitial,
  employeebranch AS branch,
  employeeoffice AS office,
  employeephone AS phone,
  true AS iscurrent,
  1 AS batchid,
  (SELECT min(datevalue) FROM DimDate) AS effectivedate,
  DATE '9999-12-31' AS enddate
FROM read_csv(
  $hr_path,
  delim = ',',
  header = false,
  quote = '',
  escape = '',
  nullstr = '',
  columns = {
    'employeeid': 'BIGINT',
    'managerid': 'BIGINT',
    'employeefirstname': 'VARCHAR',
    'employeelastname': 'VARCHAR',
    'employeemi': 'VARCHAR',
    'employeejobcode': 'INTEGER',
    'employeebranch': 'VARCHAR',
    'employeeoffice': 'VARCHAR',
    'employeephone': 'VARCHAR'
  }
)
WHERE employeejobcode = 314
