# Market App

## Domain

The **market** app is the analytics and historical tracking layer of the Rental Index. It stores daily snapshots, aggregated summaries, and price change events — the data that makes this an "index" rather than just a database mirror.

This app replaces the Google Sheets tabs: **InventoryLog**, **DailyStats**, **DailySummary**, **WeeklySummary**, and the **Monthly Report**.

All data is stored **permanently** for long-term trend analysis.

## Models

### DailyUnitSnapshot
Daily snapshot of a unit's market state. One row per unit per day. Replaces the old InventoryLog sheet.
- `unit` — **FK** to properties.Unit (CASCADE)
- `snapshot_date` — Date of capture
- `listed_price` — Asking price at time of snapshot
- `days_on_market` — DOM calculated at snapshot time
- `status` — Active / Inactive / Leased / Off Market
- `bedrooms`, `bathrooms`, `square_feet` — Unit details frozen at snapshot time
- `date_listed`, `date_off_market`
- **Unique constraint:** (`unit`, `snapshot_date`)

### DailyMarketStats
Aggregated daily statistics across all active units. One row per day. Replaces the old DailyStats tab.
- `snapshot_date` — Date (unique)
- `active_unit_count` — Number of active/listed units
- `average_dom` — Average days on market across active units
- `average_price` — Average listed price
- `count_30_plus_dom` — Number of units with 30+ DOM (stale inventory)

### DailyLeasingSummary
Per-unit daily leasing activity. Replaces the old DailySummary sheet.
- `summary_date` — Date
- `unit` — **FK** to properties.Unit (CASCADE)
- `leads_count`, `showings_completed_count`, `showings_missed_count`, `applications_count`
- `property_display_name` — Frozen display name for reporting
- **Unique constraint:** (`summary_date`, `unit`)

### WeeklyLeasingSummary
Per-unit weekly leasing activity with conversion ratios. Replaces the old WeeklySummary sheet.
- `week_ending` — End date of the 7-day period
- `unit` — **FK** to properties.Unit (CASCADE)
- `leads_count`, `showings_completed_count`, `showings_missed_count`, `applications_count`
- `lead_to_show_rate`, `show_to_app_rate` — Conversion percentages
- **Unique constraint:** (`week_ending`, `unit`)

### MonthlyMarketReport
Monthly aggregated report. Replaces the monthly Slack report from Apps Script.
- `report_month` — First day of month (unique)
- Market averages: `average_dom`, `average_price`, `average_30_plus_dom_count`
- Leasing totals: `total_leads`, `total_showings`, `total_missed_showings`, `total_applications`
- Conversion metrics: `lead_to_show_rate`, `show_to_app_rate`

### PriceDrop
Tracks price changes on units over time. Detected by comparing consecutive daily snapshots.
- `unit` — **FK** to properties.Unit (CASCADE)
- `previous_price`, `new_price`, `change_amount`, `change_percent`
- `detected_date`

## Key Relationships
- All summary/snapshot models → properties.Unit (FK)
- DailyLeasingSummary is derived from leasing.LeasingEvent data
- DailyMarketStats is derived from DailyUnitSnapshot data
- PriceDrop is derived from consecutive DailyUnitSnapshot records

## Future Services
- **InventorySnapshotService** — Daily job to fetch all units from RentEngine and create DailyUnitSnapshot rows (replaces Apps Script `runDailyUpdate`)
- **DailyStatsAggregator** — Compute DailyMarketStats from that day's snapshots
- **DailyLeasingSummaryService** — Aggregate yesterday's LeasingEvents into per-unit summaries (replaces `runDailySummary`)
- **WeeklyLeasingSummaryService** — Aggregate last 7 days with conversion ratios (replaces `runWeeklySummary`)
- **MonthlyReportService** — Generate monthly averages from DailyStats (replaces `runMonthlyReport`)
- **PriceChangeDetector** — Compare today's snapshot vs yesterday's, create PriceDrop records on delta
- **SlackReporter** — Send daily/weekly/monthly Slack messages (replaces all Apps Script `sendSlackMessage` calls)
- **CustomReportService** — On-demand reporting for arbitrary date ranges (replaces `runCustomReport`)
