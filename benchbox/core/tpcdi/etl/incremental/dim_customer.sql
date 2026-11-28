INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT
  current_timestamp,
  $batch_id,
  'DimCustomer',
  'Invalid customer tier',
  'Alert',
  concat('C_ID = ', customerid, ', C_TIER = ', tier)
FROM CustomerRaw
WHERE batchid = $batch_id
  AND (tier IS NULL OR tier NOT IN (1, 2, 3));

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT
  current_timestamp,
  $batch_id,
  'DimCustomer',
  'DOB out of range',
  'Alert',
  concat('C_ID = ', c.customerid, ', C_DOB = ', c.dob)
FROM CustomerRaw AS c
JOIN BatchDate AS b ON b.batchid = $batch_id
WHERE c.batchid = $batch_id
  AND (c.dob < b.batchdate - INTERVAL 100 YEAR OR c.dob > b.batchdate);

CREATE TEMPORARY TABLE customer_changes AS
WITH latest AS (
  SELECT *
  FROM CustomerRaw
  WHERE batchid = $batch_id
  QUALIFY row_number() OVER (PARTITION BY customerid ORDER BY cdc_dsn DESC) = 1
),
prospect_values AS (
  SELECT
    1 AS source_rank,
    agencyid,
    upper(lastname) AS lastname,
    upper(firstname) AS firstname,
    upper(addressline1) AS addressline1,
    upper(coalesce(addressline2, '')) AS addressline2,
    upper(postalcode) AS postalcode,
    creditrating,
    networth,
    marketingnameplate
  FROM ProspectRaw
  WHERE batchid = $batch_id
  UNION ALL
  SELECT
    2,
    AgencyID,
    upper(LastName),
    upper(FirstName),
    upper(AddressLine1),
    upper(coalesce(AddressLine2, '')),
    upper(PostalCode),
    CreditRating,
    NetWorth,
    MarketingNameplate
  FROM Prospect
),
demographics AS (
  SELECT *
  FROM prospect_values
  QUALIFY row_number() OVER (
    PARTITION BY lastname, firstname, addressline1, addressline2, postalcode
    ORDER BY source_rank, agencyid DESC
  ) = 1
),
batch_demographics AS (
  SELECT *
  FROM prospect_values
  WHERE source_rank = 1
  QUALIFY row_number() OVER (
    PARTITION BY lastname, firstname, addressline1, addressline2, postalcode
    ORDER BY agencyid DESC
  ) = 1
),
customer_update AS (
  SELECT
    c.customerid,
    c.taxid,
    st.ST_NAME AS status,
    c.lastname,
    c.firstname,
    c.middleinitial,
    CASE WHEN upper(c.gender) IN ('M', 'F') THEN upper(c.gender) ELSE 'U' END AS gender,
    c.tier,
    c.dob,
    c.addressline1,
    c.addressline2,
    c.postalcode,
    c.city,
    c.stateprov,
    c.country,
    c.phone1,
    c.phone2,
    c.phone3,
    c.email1,
    c.email2,
    nat.TX_NAME AS nationaltaxratedesc,
    nat.TX_RATE AS nationaltaxrate,
    lcl.TX_NAME AS localtaxratedesc,
    lcl.TX_RATE AS localtaxrate,
    d.agencyid,
    d.creditrating,
    d.networth,
    d.marketingnameplate
  FROM latest AS c
  JOIN StatusType AS st ON st.ST_ID = c.status
  LEFT JOIN TaxRate AS nat ON nat.TX_ID = c.nat_tx_id
  LEFT JOIN TaxRate AS lcl ON lcl.TX_ID = c.lcl_tx_id
  LEFT JOIN demographics AS d
    ON d.lastname = upper(c.lastname)
    AND d.firstname = upper(c.firstname)
    AND d.addressline1 = upper(c.addressline1)
    AND d.addressline2 = upper(coalesce(c.addressline2, ''))
    AND d.postalcode = upper(c.postalcode)
),
prospect_only AS (
  SELECT
    c.CustomerID,
    c.TaxID,
    c.Status,
    c.LastName,
    c.FirstName,
    c.MiddleInitial,
    c.Gender,
    c.Tier,
    c.DOB,
    c.AddressLine1,
    c.AddressLine2,
    c.PostalCode,
    c.City,
    c.StateProv,
    c.Country,
    c.Phone1,
    c.Phone2,
    c.Phone3,
    c.Email1,
    c.Email2,
    c.NationalTaxRateDesc,
    c.NationalTaxRate,
    c.LocalTaxRateDesc,
    c.LocalTaxRate,
    p.agencyid,
    p.creditrating,
    p.networth,
    p.marketingnameplate
  FROM DimCustomer AS c
  JOIN batch_demographics AS p
    ON p.lastname = upper(c.LastName)
    AND p.firstname = upper(c.FirstName)
    AND p.addressline1 = upper(c.AddressLine1)
    AND p.addressline2 = upper(coalesce(c.AddressLine2, ''))
    AND p.postalcode = upper(c.PostalCode)
  WHERE c.IsCurrent
    AND c.CustomerID NOT IN (SELECT customerid FROM latest)
    AND (
      p.agencyid IS DISTINCT FROM c.AgencyID
      OR p.creditrating IS DISTINCT FROM c.CreditRating
      OR p.networth IS DISTINCT FROM c.NetWorth
      OR p.marketingnameplate IS DISTINCT FROM c.MarketingNameplate
    )
)
SELECT * FROM customer_update
UNION ALL
SELECT * FROM prospect_only;

UPDATE DimCustomer
SET IsCurrent = false, EndDate = (SELECT batchdate FROM BatchDate WHERE batchid = $batch_id)
WHERE IsCurrent AND CustomerID IN (SELECT customerid FROM customer_changes);

INSERT INTO DimCustomer (
  SK_CustomerID, CustomerID, TaxID, Status, LastName, FirstName, MiddleInitial, Gender, Tier, DOB,
  AddressLine1, AddressLine2, PostalCode, City, StateProv, Country, Phone1, Phone2, Phone3, Email1, Email2,
  NationalTaxRateDesc, NationalTaxRate, LocalTaxRateDesc, LocalTaxRate, AgencyID, CreditRating, NetWorth,
  MarketingNameplate, IsCurrent, BatchID, EffectiveDate, EndDate
)
SELECT
  CAST(strftime(b.batchdate, '%Y%m%d') || CAST(c.customerid AS VARCHAR) AS BIGINT),
  c.customerid,
  c.taxid,
  c.status,
  c.lastname,
  c.firstname,
  c.middleinitial,
  c.gender,
  c.tier,
  c.dob,
  c.addressline1,
  c.addressline2,
  c.postalcode,
  c.city,
  c.stateprov,
  c.country,
  c.phone1,
  c.phone2,
  c.phone3,
  c.email1,
  c.email2,
  c.nationaltaxratedesc,
  c.nationaltaxrate,
  c.localtaxratedesc,
  c.localtaxrate,
  c.agencyid,
  c.creditrating,
  c.networth,
  c.marketingnameplate,
  true,
  $batch_id,
  b.batchdate,
  DATE '9999-12-31'
FROM customer_changes AS c
JOIN BatchDate AS b ON b.batchid = $batch_id;

DROP TABLE customer_changes
