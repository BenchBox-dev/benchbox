CREATE OR REPLACE TABLE CustomerRaw AS
WITH customer AS (
  SELECT
    * EXCLUDE (filename),
    CAST(regexp_extract(filename, '[/\\]Batch([0-9]+)[/\\][^/\\]+$', 1) AS INTEGER) AS batchid
  FROM read_csv(
    $customer_path,
    delim = '|',
    header = false,
    quote = '',
    escape = '',
    nullstr = '',
    filename = true,
    columns = {
      'cdc_flag': 'VARCHAR',
      'cdc_dsn': 'BIGINT',
      'customerid': 'BIGINT',
      'taxid': 'VARCHAR',
      'status': 'VARCHAR',
      'lastname': 'VARCHAR',
      'firstname': 'VARCHAR',
      'middleinitial': 'VARCHAR',
      'gender': 'VARCHAR',
      'tier': 'SMALLINT',
      'dob': 'DATE',
      'addressline1': 'VARCHAR',
      'addressline2': 'VARCHAR',
      'postalcode': 'VARCHAR',
      'city': 'VARCHAR',
      'stateprov': 'VARCHAR',
      'country': 'VARCHAR',
      'phone1_ctry': 'VARCHAR',
      'phone1_area': 'VARCHAR',
      'phone1_local': 'VARCHAR',
      'phone1_ext': 'VARCHAR',
      'phone2_ctry': 'VARCHAR',
      'phone2_area': 'VARCHAR',
      'phone2_local': 'VARCHAR',
      'phone2_ext': 'VARCHAR',
      'phone3_ctry': 'VARCHAR',
      'phone3_area': 'VARCHAR',
      'phone3_local': 'VARCHAR',
      'phone3_ext': 'VARCHAR',
      'email1': 'VARCHAR',
      'email2': 'VARCHAR',
      'lcl_tx_id': 'VARCHAR',
      'nat_tx_id': 'VARCHAR'
    }
  )
)
SELECT
  cdc_flag,
  cdc_dsn,
  customerid,
  taxid,
  status,
  lastname,
  firstname,
  middleinitial,
  gender,
  tier,
  dob,
  addressline1,
  addressline2,
  postalcode,
  city,
  stateprov,
  country,
  CASE
    WHEN phone1_local IS NOT NULL THEN concat(
      CASE WHEN phone1_ctry IS NOT NULL AND phone1_area IS NOT NULL THEN '+' || phone1_ctry || ' ' ELSE '' END,
      CASE WHEN phone1_area IS NOT NULL THEN '(' || phone1_area || ') ' ELSE '' END,
      phone1_local,
      coalesce(phone1_ext, '')
    )
  END AS phone1,
  CASE
    WHEN phone2_local IS NOT NULL THEN concat(
      CASE WHEN phone2_ctry IS NOT NULL AND phone2_area IS NOT NULL THEN '+' || phone2_ctry || ' ' ELSE '' END,
      CASE WHEN phone2_area IS NOT NULL THEN '(' || phone2_area || ') ' ELSE '' END,
      phone2_local,
      coalesce(phone2_ext, '')
    )
  END AS phone2,
  CASE
    WHEN phone3_local IS NOT NULL THEN concat(
      CASE WHEN phone3_ctry IS NOT NULL AND phone3_area IS NOT NULL THEN '+' || phone3_ctry || ' ' ELSE '' END,
      CASE WHEN phone3_area IS NOT NULL THEN '(' || phone3_area || ') ' ELSE '' END,
      phone3_local,
      coalesce(phone3_ext, '')
    )
  END AS phone3,
  email1,
  email2,
  lcl_tx_id,
  nat_tx_id,
  batchid
FROM customer
