# Properties App

## Domain

The **properties** app is the core identity layer for the Vesta Rental Index. It holds the canonical representation of every physical asset Vesta manages — properties, units, portfolios, and owners. Every other app references this one.

Properties and units carry **dual external IDs** (`rentvine_id` and `rentengine_id`) so the system can cross-reference data between RentVine (portfolio/management source of truth) and RentEngine (leasing/market activity source of truth).

## Models

### Portfolio
Owner portfolio from RentVine. Groups properties under a single ownership entity.
- `rentvine_id` — RentVine portfolioID (unique)
- `name`, `is_active` — Basic identity
- `reserve_amount`, `additional_reserve_amount` — Financial reserves
- `fiscal_year_end_month` — Accounting calendar
- `raw_data` — Full RentVine API response (JSON)

### Owner
Property owner from RentVine contacts (contactTypeID=1).
- `rentvine_contact_id` — RentVine contactID (unique)
- `name`, `first_name`, `last_name`, `email`, `phone` — Contact info
- `portfolios` — **M2M** to Portfolio (owners can have multiple portfolios)
- `raw_data` — Full API response

### Property
Canonical property record. Cross-referenced across RentVine and RentEngine.
- `rentvine_id`, `rentengine_id` — Dual external IDs for cross-referencing
- `portfolio` — **FK** to Portfolio
- `property_type` — Single Family, Apartment, Condo, Townhouse, Duplex, Multiplex, Loft, Mobile Home, Commercial, Garage
- `is_multi_unit` — Whether property has multiple units
- Full address fields: `street_number`, `street_name`, `address_line_1/2`, `city`, `state`, `postal_code`, `country`, `latitude`, `longitude`, `county`
- `year_built`, `is_active`
- RentVine management: `management_fee_setting_id`, `maintenance_limit_amount`, `reserve_amount`, contract/insurance/warranty dates
- `raw_data` — Full API response

### Unit
Canonical unit record. Single-unit properties have one; multi-unit have many.
- `rentvine_id`, `rentengine_id` — Dual external IDs
- `property` — **FK** to Property (CASCADE)
- Address fields (may differ from parent property for multi-unit)
- `bedrooms`, `full_bathrooms`, `half_bathrooms`, `square_feet`
- `target_rental_rate`, `deposit`
- `multifamily_property` — **FK** to MultifamilyProperty (for RentEngine syndication)
- `raw_data` — Full API response

### MultifamilyProperty
RentEngine record for paid advertising syndication (Zillow, Apartments.com).
- `rentengine_id` — RentEngine ID (unique)
- `name`, `text_address`
- `raw_data` — Full API response

### Floorplan
RentEngine floorplan for syndication, linked to a multifamily property.
- `rentengine_id` — RentEngine ID (unique)
- `multifamily_property` — **FK** to MultifamilyProperty
- `raw_data` — Full API response

## Key Relationships
- Portfolio → Properties (one-to-many)
- Owner ↔ Portfolio (many-to-many)
- Property → Units (one-to-many)
- MultifamilyProperty → Floorplans (one-to-many)
- Unit → MultifamilyProperty (optional FK for syndication)

## Future Services
- **PropertySyncService** — Daily pull from RentVine `/properties/export` and RentEngine `/units` to upsert canonical records
- **CrossReferenceService** — Match RentVine properties/units to RentEngine units by address for dual-ID linking
- **PortfolioSyncService** — Sync portfolios and owner contacts from RentVine
