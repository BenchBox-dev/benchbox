INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'Prospect', 'Source rows', 'Status', CAST(count(*) AS VARCHAR)
FROM ProspectRaw
WHERE batchid = $batch_id;

CREATE TEMPORARY TABLE prospect_changes AS
SELECT
  p.agencyid,
  p.lastname,
  p.firstname,
  p.middleinitial,
  p.gender,
  p.addressline1,
  p.addressline2,
  p.postalcode,
  p.city,
  p.state,
  p.country,
  p.phone,
  p.income,
  p.numbercars,
  p.numberchildren,
  p.maritalstatus,
  p.age,
  p.creditrating,
  p.ownorrentflag,
  p.employer,
  p.numbercreditcards,
  p.networth,
  p.marketingnameplate,
  d.SK_DateID AS date_id,
  CASE
    WHEN t.AgencyID IS NULL THEN 'new'
    WHEN t.LastName IS DISTINCT FROM p.lastname
      OR t.FirstName IS DISTINCT FROM p.firstname
      OR t.MiddleInitial IS DISTINCT FROM p.middleinitial
      OR t.Gender IS DISTINCT FROM p.gender
      OR t.AddressLine1 IS DISTINCT FROM p.addressline1
      OR t.AddressLine2 IS DISTINCT FROM p.addressline2
      OR t.PostalCode IS DISTINCT FROM p.postalcode
      OR t.City IS DISTINCT FROM p.city
      OR t.State IS DISTINCT FROM p.state
      OR t.Country IS DISTINCT FROM p.country
      OR t.Phone IS DISTINCT FROM p.phone
      OR t.Income IS DISTINCT FROM p.income
      OR t.NumberCars IS DISTINCT FROM p.numbercars
      OR t.NumberChildren IS DISTINCT FROM p.numberchildren
      OR t.MaritalStatus IS DISTINCT FROM p.maritalstatus
      OR t.Age IS DISTINCT FROM p.age
      OR t.CreditRating IS DISTINCT FROM p.creditrating
      OR t.OwnOrRentFlag IS DISTINCT FROM p.ownorrentflag
      OR t.Employer IS DISTINCT FROM p.employer
      OR t.NumberCreditCards IS DISTINCT FROM p.numbercreditcards
      OR t.NetWorth IS DISTINCT FROM p.networth
      OR t.MarketingNameplate IS DISTINCT FROM p.marketingnameplate
      THEN 'changed'
    ELSE 'seen'
  END AS change_kind
FROM ProspectRaw AS p
JOIN BatchDate AS b ON b.batchid = $batch_id
JOIN DimDate AS d ON d.DateValue = b.batchdate
LEFT JOIN Prospect AS t ON t.AgencyID = p.agencyid
WHERE p.batchid = $batch_id;

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'Prospect', 'Inserted rows', 'Status', CAST(count(*) AS VARCHAR)
FROM prospect_changes
WHERE change_kind = 'new';

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, $batch_id, 'Prospect', 'Updated rows', 'Status', CAST(count(*) AS VARCHAR)
FROM prospect_changes
WHERE change_kind = 'changed';

UPDATE Prospect
SET
  SK_RecordDateID = c.date_id,
  SK_UpdateDateID = c.date_id,
  BatchID = $batch_id,
  LastName = c.lastname,
  FirstName = c.firstname,
  MiddleInitial = c.middleinitial,
  Gender = c.gender,
  AddressLine1 = c.addressline1,
  AddressLine2 = c.addressline2,
  PostalCode = c.postalcode,
  City = c.city,
  State = c.state,
  Country = c.country,
  Phone = c.phone,
  Income = c.income,
  NumberCars = c.numbercars,
  NumberChildren = c.numberchildren,
  MaritalStatus = c.maritalstatus,
  Age = c.age,
  CreditRating = c.creditrating,
  OwnOrRentFlag = c.ownorrentflag,
  Employer = c.employer,
  NumberCreditCards = c.numbercreditcards,
  NetWorth = c.networth,
  MarketingNameplate = c.marketingnameplate
FROM prospect_changes AS c
WHERE Prospect.AgencyID = c.agencyid AND c.change_kind = 'changed';

UPDATE Prospect
SET SK_RecordDateID = c.date_id
FROM prospect_changes AS c
WHERE Prospect.AgencyID = c.agencyid AND c.change_kind = 'seen';

INSERT INTO Prospect (
  AgencyID, SK_RecordDateID, SK_UpdateDateID, BatchID, IsCustomer, LastName, FirstName, MiddleInitial, Gender,
  AddressLine1, AddressLine2, PostalCode, City, State, Country, Phone, Income, NumberCars, NumberChildren,
  MaritalStatus, Age, CreditRating, OwnOrRentFlag, Employer, NumberCreditCards, NetWorth, MarketingNameplate
)
SELECT
  agencyid,
  date_id,
  date_id,
  $batch_id,
  false,
  lastname,
  firstname,
  middleinitial,
  gender,
  addressline1,
  addressline2,
  postalcode,
  city,
  state,
  country,
  phone,
  income,
  numbercars,
  numberchildren,
  maritalstatus,
  age,
  creditrating,
  ownorrentflag,
  employer,
  numbercreditcards,
  networth,
  marketingnameplate
FROM prospect_changes
WHERE change_kind = 'new';

CREATE TEMPORARY TABLE customer_flags AS
WITH current_customers AS (
  SELECT DISTINCT
    upper(c.LastName) AS lastname,
    upper(c.FirstName) AS firstname,
    upper(c.AddressLine1) AS addressline1,
    upper(coalesce(c.AddressLine2, '')) AS addressline2,
    upper(c.PostalCode) AS postalcode
  FROM DimCustomer AS c
  JOIN StatusType AS st ON st.ST_ID = 'ACTV' AND st.ST_NAME = c.Status
  WHERE c.IsCurrent
)
SELECT p.AgencyID AS agencyid, cc.lastname IS NOT NULL AS is_customer
FROM Prospect AS p
LEFT JOIN current_customers AS cc
  ON cc.lastname = upper(p.LastName)
  AND cc.firstname = upper(p.FirstName)
  AND cc.addressline1 = upper(p.AddressLine1)
  AND cc.addressline2 = upper(coalesce(p.AddressLine2, ''))
  AND cc.postalcode = upper(p.PostalCode);

UPDATE Prospect
SET IsCustomer = f.is_customer
FROM customer_flags AS f
WHERE Prospect.AgencyID = f.agencyid AND Prospect.IsCustomer <> f.is_customer;

DROP TABLE customer_flags;

DROP TABLE prospect_changes
