CREATE OR REPLACE TABLE ProspectRaw AS
WITH prospect AS (
  SELECT
    * EXCLUDE (filename),
    CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
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
),
tagged AS (
  SELECT
    *,
    concat(
      CASE WHEN networth > 1000000 OR income > 200000 THEN 'HighValue+' ELSE '' END,
      CASE WHEN numberchildren > 3 OR numbercreditcards > 5 THEN 'Expenses+' ELSE '' END,
      CASE WHEN age > 45 THEN 'Boomer+' ELSE '' END,
      CASE WHEN income < 50000 OR creditrating < 600 OR networth < 100000 THEN 'MoneyAlert+' ELSE '' END,
      CASE WHEN numbercars > 3 OR numbercreditcards > 7 THEN 'Spender+' ELSE '' END,
      CASE WHEN age < 25 AND networth > 1000000 THEN 'Inherited+' ELSE '' END
    ) AS tags
  FROM prospect
)
SELECT * EXCLUDE (tags), nullif(rtrim(tags, '+'), '') AS marketingnameplate
FROM tagged
