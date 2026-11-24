CREATE OR REPLACE TABLE CustomerMgmtRaw AS
WITH actions AS (
  SELECT unnest(xml_extract_elements(xml, '//*[local-name()=''Action'']')) AS node
  FROM read_xml_objects($customer_mgmt_path, maximum_file_size => 2000000000)
),
fields AS (
  SELECT
    xml_extract_text(node, '/*/@ActionType')[1] AS actiontype,
    xml_extract_text(node, '/*/@ActionTS')[1] AS actionts,
    xml_extract_text(node, '//Customer/@C_ID')[1] AS customerid,
    xml_extract_text(node, '//Customer/@C_TAX_ID')[1] AS taxid,
    xml_extract_text(node, '//Customer/@C_GNDR')[1] AS gender,
    xml_extract_text(node, '//Customer/@C_TIER')[1] AS tier,
    xml_extract_text(node, '//Customer/@C_DOB')[1] AS dob,
    xml_extract_text(node, '//Account/@CA_ID')[1] AS accountid,
    xml_extract_text(node, '//Account/@CA_TAX_ST')[1] AS taxstatus,
    xml_extract_text(node, '//Account/CA_B_ID')[1] AS brokerid,
    xml_extract_text(node, '//Account/CA_NAME')[1] AS accountdesc,
    xml_extract_text(node, '//Name/C_L_NAME')[1] AS lastname,
    xml_extract_text(node, '//Name/C_F_NAME')[1] AS firstname,
    xml_extract_text(node, '//Name/C_M_NAME')[1] AS middleinitial,
    xml_extract_text(node, '//Address/C_ADLINE1')[1] AS addressline1,
    xml_extract_text(node, '//Address/C_ADLINE2')[1] AS addressline2,
    xml_extract_text(node, '//Address/C_ZIPCODE')[1] AS postalcode,
    xml_extract_text(node, '//Address/C_CITY')[1] AS city,
    xml_extract_text(node, '//Address/C_STATE_PROV')[1] AS stateprov,
    xml_extract_text(node, '//Address/C_CTRY')[1] AS country,
    xml_extract_text(node, '//C_PHONE_1/C_CTRY_CODE')[1] AS phone1_ctry,
    xml_extract_text(node, '//C_PHONE_1/C_AREA_CODE')[1] AS phone1_area,
    xml_extract_text(node, '//C_PHONE_1/C_LOCAL')[1] AS phone1_local,
    xml_extract_text(node, '//C_PHONE_1/C_EXT')[1] AS phone1_ext,
    xml_extract_text(node, '//C_PHONE_2/C_CTRY_CODE')[1] AS phone2_ctry,
    xml_extract_text(node, '//C_PHONE_2/C_AREA_CODE')[1] AS phone2_area,
    xml_extract_text(node, '//C_PHONE_2/C_LOCAL')[1] AS phone2_local,
    xml_extract_text(node, '//C_PHONE_2/C_EXT')[1] AS phone2_ext,
    xml_extract_text(node, '//C_PHONE_3/C_CTRY_CODE')[1] AS phone3_ctry,
    xml_extract_text(node, '//C_PHONE_3/C_AREA_CODE')[1] AS phone3_area,
    xml_extract_text(node, '//C_PHONE_3/C_LOCAL')[1] AS phone3_local,
    xml_extract_text(node, '//C_PHONE_3/C_EXT')[1] AS phone3_ext,
    xml_extract_text(node, '//ContactInfo/C_PRIM_EMAIL')[1] AS email1,
    xml_extract_text(node, '//ContactInfo/C_ALT_EMAIL')[1] AS email2,
    xml_extract_text(node, '//TaxInfo/C_LCL_TX_ID')[1] AS lcl_tx_id,
    xml_extract_text(node, '//TaxInfo/C_NAT_TX_ID')[1] AS nat_tx_id
  FROM actions
)
SELECT
  try_cast(nullif(customerid, '') AS BIGINT) AS customerid,
  try_cast(nullif(accountid, '') AS BIGINT) AS accountid,
  try_cast(nullif(brokerid, '') AS BIGINT) AS brokerid,
  nullif(taxid, '') AS taxid,
  nullif(accountdesc, '') AS accountdesc,
  try_cast(nullif(taxstatus, '') AS SMALLINT) AS taxstatus,
  nullif(lastname, '') AS lastname,
  nullif(firstname, '') AS firstname,
  nullif(middleinitial, '') AS middleinitial,
  nullif(gender, '') AS gender,
  try_cast(nullif(tier, '') AS SMALLINT) AS tier,
  try_cast(nullif(dob, '') AS DATE) AS dob,
  nullif(addressline1, '') AS addressline1,
  nullif(addressline2, '') AS addressline2,
  nullif(postalcode, '') AS postalcode,
  nullif(city, '') AS city,
  nullif(stateprov, '') AS stateprov,
  nullif(country, '') AS country,
  CASE
    WHEN nullif(phone1_local, '') IS NOT NULL THEN concat(
      CASE WHEN nullif(phone1_ctry, '') IS NOT NULL THEN '+' || phone1_ctry || ' ' ELSE '' END,
      CASE WHEN nullif(phone1_area, '') IS NOT NULL THEN '(' || phone1_area || ') ' ELSE '' END,
      phone1_local,
      coalesce(phone1_ext, '')
    )
  END AS phone1,
  CASE
    WHEN nullif(phone2_local, '') IS NOT NULL THEN concat(
      CASE WHEN nullif(phone2_ctry, '') IS NOT NULL THEN '+' || phone2_ctry || ' ' ELSE '' END,
      CASE WHEN nullif(phone2_area, '') IS NOT NULL THEN '(' || phone2_area || ') ' ELSE '' END,
      phone2_local,
      coalesce(phone2_ext, '')
    )
  END AS phone2,
  CASE
    WHEN nullif(phone3_local, '') IS NOT NULL THEN concat(
      CASE WHEN nullif(phone3_ctry, '') IS NOT NULL THEN '+' || phone3_ctry || ' ' ELSE '' END,
      CASE WHEN nullif(phone3_area, '') IS NOT NULL THEN '(' || phone3_area || ') ' ELSE '' END,
      phone3_local,
      coalesce(phone3_ext, '')
    )
  END AS phone3,
  nullif(email1, '') AS email1,
  nullif(email2, '') AS email2,
  nullif(lcl_tx_id, '') AS lcl_tx_id,
  nullif(nat_tx_id, '') AS nat_tx_id,
  try_cast(nullif(actionts, '') AS TIMESTAMP) AS update_ts,
  actiontype
FROM fields
