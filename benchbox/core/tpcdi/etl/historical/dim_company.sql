INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT
  current_timestamp,
  1,
  'DimCompany',
  'Invalid SPRating',
  'Alert',
  concat('CO_ID = ', trim(substring(value, 61, 10)), ', CO_SP_RATE = ', trim(substring(value, 77, 4)))
FROM FinWire
WHERE rectype = 'CMP'
  AND trim(substring(value, 77, 4)) NOT IN (
    'AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-', 'BB+', 'BB', 'BB-',
    'B+', 'B', 'B-', 'CCC+', 'CCC', 'CCC-', 'CC', 'C', 'D'
  );

INSERT INTO DimCompany (
  SK_CompanyID, CompanyID, Status, Name, Industry, SPrating, isLowGrade, CEO, AddressLine1, AddressLine2,
  PostalCode, City, StateProv, Country, Description, FoundingDate, IsCurrent, BatchID, EffectiveDate, EndDate
)
WITH cmp AS (
  SELECT
    recdate AS effectivedate,
    trim(substring(value, 1, 60)) AS name,
    CAST(trim(substring(value, 61, 10)) AS BIGINT) AS companyid,
    trim(substring(value, 71, 4)) AS status,
    trim(substring(value, 75, 2)) AS industryid,
    trim(substring(value, 77, 4)) AS sprating,
    nullif(trim(substring(value, 81, 8)), '') AS foundingdate,
    trim(substring(value, 89, 80)) AS addressline1,
    trim(substring(value, 169, 80)) AS addressline2,
    trim(substring(value, 249, 12)) AS postalcode,
    trim(substring(value, 261, 25)) AS city,
    trim(substring(value, 286, 20)) AS stateprov,
    trim(substring(value, 306, 24)) AS country,
    trim(substring(value, 330, 46)) AS ceo,
    trim(substring(value, 376, 150)) AS description
  FROM FinWire
  WHERE rectype = 'CMP'
),
rated AS (
  SELECT
    *,
    sprating IN (
      'AAA', 'AA+', 'AA', 'AA-', 'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-', 'BB+', 'BB', 'BB-',
      'B+', 'B', 'B-', 'CCC+', 'CCC', 'CCC-', 'CC', 'C', 'D'
    ) AS valid_rating,
    coalesce(
      lead(effectivedate) OVER (PARTITION BY companyid ORDER BY effectivedate),
      DATE '9999-12-31'
    ) AS enddate
  FROM cmp
)
SELECT
  CAST(strftime(r.effectivedate, '%Y%m%d') || CAST(r.companyid AS VARCHAR) AS BIGINT),
  r.companyid,
  st.ST_NAME,
  r.name,
  ind.IN_NAME,
  CASE WHEN r.valid_rating THEN r.sprating END,
  CASE WHEN r.valid_rating THEN NOT (r.sprating LIKE 'A%' OR r.sprating LIKE 'BBB%') END,
  r.ceo,
  r.addressline1,
  r.addressline2,
  r.postalcode,
  r.city,
  r.stateprov,
  r.country,
  r.description,
  CAST(strptime(r.foundingdate, '%Y%m%d') AS DATE),
  r.enddate = DATE '9999-12-31',
  1,
  r.effectivedate,
  r.enddate
FROM rated AS r
JOIN StatusType AS st ON st.ST_ID = r.status
JOIN Industry AS ind ON ind.IN_ID = r.industryid
WHERE r.effectivedate < r.enddate
