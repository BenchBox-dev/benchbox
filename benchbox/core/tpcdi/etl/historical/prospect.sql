INSERT INTO Prospect (
  AgencyID, SK_RecordDateID, SK_UpdateDateID, BatchID, IsCustomer, LastName, FirstName, MiddleInitial, Gender,
  AddressLine1, AddressLine2, PostalCode, City, State, Country, Phone, Income, NumberCars, NumberChildren,
  MaritalStatus, Age, CreditRating, OwnOrRentFlag, Employer, NumberCreditCards, NetWorth, MarketingNameplate
)
WITH current_customers AS (
  SELECT DISTINCT
    upper(LastName) AS lastname,
    upper(FirstName) AS firstname,
    upper(AddressLine1) AS addressline1,
    upper(coalesce(AddressLine2, '')) AS addressline2,
    upper(PostalCode) AS postalcode
  FROM DimCustomer
  WHERE IsCurrent
)
SELECT
  p.agencyid,
  d.SK_DateID,
  d.SK_DateID,
  1,
  c.lastname IS NOT NULL,
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
  p.marketingnameplate
FROM ProspectRaw AS p
JOIN BatchDate AS b ON b.batchid = p.batchid
JOIN DimDate AS d ON d.DateValue = b.batchdate
LEFT JOIN current_customers AS c
  ON c.lastname = upper(p.lastname)
  AND c.firstname = upper(p.firstname)
  AND c.addressline1 = upper(p.addressline1)
  AND c.addressline2 = upper(coalesce(p.addressline2, ''))
  AND c.postalcode = upper(p.postalcode)
WHERE p.batchid = 1;

INSERT INTO DImessages (MessageDateAndTime, BatchID, MessageSource, MessageText, MessageType, MessageData)
SELECT current_timestamp, 1, 'Prospect', 'Inserted rows', 'Status', CAST(count(*) AS VARCHAR)
FROM Prospect
WHERE BatchID = 1
