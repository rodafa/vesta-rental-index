# Known Issues

## Collected overstated by applied rent concessions (V1)

**Product:** Owner Distribution Email
**Severity:** Low — not a send blocker

The distribution email's "Collected" column reads `amountPaid` from RentVine
type-1 charge records. When the PM grants a rent concession (type-3 credit on
rent account 13/14), RentVine applies the credit to the charge's `amountPaid`,
making it appear that more cash was collected than actually was.

**Impact:** Collected is overstated by the credit amount on affected properties.
Observed at ~2% max on a few portfolios per month (3 credits totaling $450
across 88 portfolios in May 2026). Distribution figures are unaffected — they
come from type-16 records, not from the rent table.

**Not affected:** "Rent Credit Overpayment" type credits, which sit as separate
balance entries and do not inflate `amountPaid`.

**Proper fix:** Derive collected from type-2 cash payment receipts allocated to
rent accounts, net of type-4 bounces. This also correctly handles overpayment
credits. Requires RentVine type-2 records to carry `chargeAccountID` (currently
null) or a payment-to-charge allocation lookup — neither is available in the
current API surface.
