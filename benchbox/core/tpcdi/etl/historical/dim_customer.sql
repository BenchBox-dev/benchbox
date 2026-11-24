INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT
  current_timestamp,
  1,
  'DimCustomer',
  'Invalid customer tier',
  'Alert',
  concat('C_ID = ', customerid, ', C_TIER = ', tier)
FROM CustomerMgmtRaw
WHERE (actiontype = 'NEW' AND (tier IS NULL OR tier NOT IN (1, 2, 3)))
  OR (actiontype = 'UPDCUST' AND tier IS NOT NULL AND tier NOT IN (1, 2, 3));

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT
  current_timestamp,
  1,
  'DimCustomer',
  'DOB out of range',
  'Alert',
  concat('C_ID = ', c.customerid, ', C_DOB = ', c.dob)
FROM CustomerMgmtRaw AS c
JOIN BatchDate AS b ON b.batchid = 1
WHERE c.actiontype IN ('NEW', 'UPDCUST')
  AND (c.dob < b.batchdate - INTERVAL 100 YEAR OR c.dob > b.batchdate);

INSERT INTO DimCustomer (
  SK_CustomerID, CustomerID, TaxID, Status, LastName, FirstName, MiddleInitial, Gender, Tier, DOB,
  AddressLine1, AddressLine2, PostalCode, City, StateProv, Country, Phone1, Phone2, Phone3, Email1, Email2,
  NationalTaxRateDesc, NationalTaxRate, LocalTaxRateDesc, LocalTaxRate, AgencyID, CreditRating, NetWorth,
  MarketingNameplate, IsCurrent, BatchID, EffectiveDate, EndDate
)
WITH history AS (
  SELECT
    customerid,
    CASE WHEN actiontype = 'INACT' THEN 'INAC' ELSE 'ACTV' END AS status_id,
    last_value(taxid IGNORE NULLS) OVER w AS taxid,
    last_value(lastname IGNORE NULLS) OVER w AS lastname,
    last_value(firstname IGNORE NULLS) OVER w AS firstname,
    last_value(middleinitial IGNORE NULLS) OVER w AS middleinitial,
    last_value(gender IGNORE NULLS) OVER w AS gender,
    last_value(tier IGNORE NULLS) OVER w AS tier,
    last_value(dob IGNORE NULLS) OVER w AS dob,
    last_value(addressline1 IGNORE NULLS) OVER w AS addressline1,
    last_value(addressline2 IGNORE NULLS) OVER w AS addressline2,
    last_value(postalcode IGNORE NULLS) OVER w AS postalcode,
    last_value(city IGNORE NULLS) OVER w AS city,
    last_value(stateprov IGNORE NULLS) OVER w AS stateprov,
    last_value(country IGNORE NULLS) OVER w AS country,
    last_value(phone1 IGNORE NULLS) OVER w AS phone1,
    last_value(phone2 IGNORE NULLS) OVER w AS phone2,
    last_value(phone3 IGNORE NULLS) OVER w AS phone3,
    last_value(email1 IGNORE NULLS) OVER w AS email1,
    last_value(email2 IGNORE NULLS) OVER w AS email2,
    last_value(lcl_tx_id IGNORE NULLS) OVER w AS lcl_tx_id,
    last_value(nat_tx_id IGNORE NULLS) OVER w AS nat_tx_id,
    CAST(update_ts AS DATE) AS effectivedate,
    coalesce(
      lead(CAST(update_ts AS DATE)) OVER (PARTITION BY customerid ORDER BY update_ts),
      DATE '9999-12-31'
    ) AS enddate
  FROM CustomerMgmtRaw
  WHERE actiontype IN ('NEW', 'UPDCUST', 'INACT')
  WINDOW w AS (PARTITION BY customerid ORDER BY update_ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
)
SELECT
  CAST(strftime(h.effectivedate, '%Y%m%d') || CAST(h.customerid AS VARCHAR) AS BIGINT),
  h.customerid,
  h.taxid,
  st.ST_NAME,
  h.lastname,
  h.firstname,
  h.middleinitial,
  CASE WHEN upper(h.gender) IN ('M', 'F') THEN upper(h.gender) ELSE 'U' END,
  h.tier,
  h.dob,
  h.addressline1,
  h.addressline2,
  h.postalcode,
  h.city,
  h.stateprov,
  h.country,
  h.phone1,
  h.phone2,
  h.phone3,
  h.email1,
  h.email2,
  nat.TX_NAME,
  nat.TX_RATE,
  lcl.TX_NAME,
  lcl.TX_RATE,
  p.agencyid,
  p.creditrating,
  p.networth,
  p.marketingnameplate,
  h.enddate = DATE '9999-12-31',
  1,
  h.effectivedate,
  h.enddate
FROM history AS h
JOIN StatusType AS st ON st.ST_ID = h.status_id
LEFT JOIN TaxRate AS nat ON nat.TX_ID = h.nat_tx_id
LEFT JOIN TaxRate AS lcl ON lcl.TX_ID = h.lcl_tx_id
LEFT JOIN ProspectRaw AS p
  ON p.batchid = 1
  AND upper(p.lastname) = upper(h.lastname)
  AND upper(p.firstname) = upper(h.firstname)
  AND upper(p.addressline1) = upper(h.addressline1)
  AND upper(coalesce(p.addressline2, '')) = upper(coalesce(h.addressline2, ''))
  AND upper(p.postalcode) = upper(h.postalcode)
WHERE h.effectivedate < h.enddate
