# Lessons Learned

## RentVine Recurring Charges

### Expired charges must be filtered by endDate
- **Problem**: `_sync_rent_amount` was summing ALL `isRent` recurring charges, including expired one-time charges (pro-rated rent from move-in, old rent amounts after increases). This inflated `rent_amount` by ~8.5%.
- **Root cause**: RentVine returns all historical recurring charges for a lease, not just active ones. Charges with `endDate` in the past are no longer being billed.
- **Fix**: Filter by `endDate` — skip charges where `endDate < today`.
- **Rule**: When consuming RentVine recurring charges, always check `endDate` to determine if the charge is still active.

### RentVine isRent includes multiple account types
- RentVine's `account.isRent == "1"` flag applies to: **Rent Income**, **Pet Rent**, and **Government Assistance Rent**.
- Our `rent_amount` is gross rent (all isRent charges). `pet_rent_amount` tracks the pet rent subset.
- RentVine's UI "average rent" appears to exclude Pet Rent from their display, but the API includes it.

## Queryset Exclusion Filters

### Always apply the agreed filter logic exactly — don't simplify
- **Problem**: Plan specified excluding lawn mowing melds where `category IN (EXTERIOR, LANDSCAPING) AND description contains "mow"/"lawn care"/"grass"`. I implemented a simpler version that only filtered on description keywords without the category constraint, which would have excluded a different count of records (191 vs 197).
- **Rule**: When a filter has both a category/type constraint AND a keyword constraint, implement both as a compound `Q(category__in=[...]) & (Q(desc__icontains=...) | ...)`. Don't drop constraints for "simplicity" — the constraint scoping exists for a reason.

## Windows/Docker

### CRLF line endings break crontab in Linux containers
- **Problem**: Volume-mounted files from Windows have `\r\n` endings. Linux `crontab` command fails with "bad minute" error.
- **Fix**: Strip `\r` in container startup (`sed -i 's/\r$//'`) before installing crontab.
- Also added `cron/crontab text eol=lf` to `.gitattributes`.
