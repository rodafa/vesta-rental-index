"""
Market aggregation services. Transform raw DailyUnitSnapshot / DailyLeasingSummary
records into higher-level aggregated stats.

Each service follows a consistent pattern:
  - .run(date, ...) — compute and upsert for a single period
  - Idempotent via update_or_create
  - Returns dict with created/updated counts
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Sum

from leasing.models import Lease
from market.models import (
    DailyLeasingSummary,
    DailyMarketStats,
    DailySegmentStats,
    DailyUnitSnapshot,
    ListingCycle,
    MonthlyMarketReport,
    MonthlySegmentStats,
    PriceDrop,
    WeeklyLeasingSummary,
)

logger = logging.getLogger(__name__)


def _safe_ratio(numerator, denominator):
    """Compute ratio as Decimal, return None if denominator is zero."""
    if not denominator:
        return None
    return Decimal(str(numerator)) / Decimal(str(denominator))


def _price_band(price):
    """Bucket a listed price into a price band string."""
    if price is None:
        return "unknown"
    price = int(price)
    if price < 1000:
        return "under_1000"
    elif price < 1500:
        return "1000_1499"
    elif price < 2000:
        return "1500_1999"
    elif price < 2500:
        return "2000_2499"
    elif price < 3000:
        return "2500_2999"
    else:
        return "3000_plus"


class DailyMarketStatsAggregator:
    """Aggregate daily market stats from DailyUnitSnapshot + Lease data."""

    def run(self, target_date):
        snapshots = DailyUnitSnapshot.objects.filter(
            snapshot_date=target_date, status="active"
        )
        agg = snapshots.aggregate(
            active_count=Count("id"),
            avg_dom=Avg("days_on_market"),
            avg_price=Avg("listed_price"),
            count_30_plus=Count("id", filter=Q(days_on_market__gte=30)),
        )

        # Average portfolio rent from active leases with rent data
        rent_agg = Lease.objects.filter(
            primary_lease_status=2,
            rent_amount__gt=0,
        ).aggregate(avg_rent=Avg("rent_amount"))

        _, created = DailyMarketStats.objects.update_or_create(
            snapshot_date=target_date,
            defaults={
                "active_unit_count": agg["active_count"] or 0,
                "average_dom": int(agg["avg_dom"] or 0),
                "average_price": agg["avg_price"] or Decimal("0"),
                "count_30_plus_dom": agg["count_30_plus"] or 0,
                "average_portfolio_rent": rent_agg["avg_rent"] or Decimal("0"),
            },
        )
        action = "created" if created else "updated"
        logger.info("DailyMarketStats %s for %s: %d active units", action, target_date, agg["active_count"] or 0)
        return {"created": int(created), "updated": int(not created)}


class PriceChangeDetector:
    """Detect downward price changes by comparing consecutive daily snapshots."""

    def run(self, target_date):
        yesterday = target_date - timedelta(days=1)
        created_count = 0

        today_snapshots = DailyUnitSnapshot.objects.filter(
            snapshot_date=target_date,
            status="active",
            listed_price__isnull=False,
        ).select_related("unit")

        for snap in today_snapshots:
            try:
                prev = DailyUnitSnapshot.objects.get(
                    unit=snap.unit,
                    snapshot_date=yesterday,
                    listed_price__isnull=False,
                )
            except DailyUnitSnapshot.DoesNotExist:
                continue

            if snap.listed_price < prev.listed_price:
                drop_amount = prev.listed_price - snap.listed_price
                drop_percent = (drop_amount / prev.listed_price) * 100

                _, was_created = PriceDrop.objects.get_or_create(
                    unit=snap.unit,
                    detected_date=target_date,
                    defaults={
                        "previous_price": prev.listed_price,
                        "new_price": snap.listed_price,
                        "drop_amount": drop_amount,
                        "drop_percent": drop_percent,
                    },
                )
                if was_created:
                    created_count += 1

        logger.info("PriceChangeDetector: %d drops detected for %s", created_count, target_date)
        return {"created": created_count, "updated": 0}


class ListingCycleTracker:
    """Track listing cycles — open when unit becomes active, close when it leaves active."""

    def run(self, target_date):
        yesterday = target_date - timedelta(days=1)
        created_count = 0
        closed_count = 0

        today_snapshots = {
            s.unit_id: s
            for s in DailyUnitSnapshot.objects.filter(snapshot_date=target_date).select_related("unit")
        }
        yesterday_snapshots = {
            s.unit_id: s
            for s in DailyUnitSnapshot.objects.filter(snapshot_date=yesterday)
        }

        # Open new cycles: unit is active today but wasn't yesterday (or no yesterday snapshot)
        for unit_id, snap in today_snapshots.items():
            if snap.status != "active":
                continue

            prev = yesterday_snapshots.get(unit_id)
            if prev and prev.status == "active":
                continue  # Already active, not a new listing

            # Check no open cycle exists for this unit
            open_cycle = ListingCycle.objects.filter(
                unit_id=unit_id, leased_date__isnull=True
            ).exists()
            if open_cycle:
                continue

            ListingCycle.objects.create(
                unit_id=unit_id,
                listed_date=target_date,
                original_list_price=snap.listed_price,
            )
            created_count += 1

        # Close cycles: unit was active yesterday, not active today
        for unit_id, prev_snap in yesterday_snapshots.items():
            if prev_snap.status != "active":
                continue

            today_snap = today_snapshots.get(unit_id)
            if today_snap and today_snap.status == "active":
                continue  # Still active

            open_cycle = ListingCycle.objects.filter(
                unit_id=unit_id, leased_date__isnull=True
            ).first()
            if not open_cycle:
                continue

            # Close the cycle
            open_cycle.leased_date = target_date
            open_cycle.total_dom = (target_date - open_cycle.listed_date).days

            # Final list price from yesterday's snapshot (last active day)
            open_cycle.final_list_price = prev_snap.listed_price

            # Aggregate price drops during this cycle
            drops = PriceDrop.objects.filter(
                unit_id=unit_id,
                detected_date__gte=open_cycle.listed_date,
                detected_date__lte=target_date,
            )
            drop_agg = drops.aggregate(
                total_drops=Count("id"),
                total_amount=Sum("drop_amount"),
            )
            open_cycle.total_price_drops = drop_agg["total_drops"] or 0
            open_cycle.total_drop_amount = drop_agg["total_amount"] or Decimal("0")

            # Link signed lease amount from most recent active lease
            latest_lease = Lease.objects.filter(
                unit_id=unit_id,
                primary_lease_status=2,
                rent_amount__isnull=False,
            ).order_by("-start_date").first()
            if latest_lease:
                open_cycle.signed_lease_amount = latest_lease.rent_amount
                open_cycle.lease_start_date = latest_lease.start_date

            open_cycle.save()
            closed_count += 1

        logger.info(
            "ListingCycleTracker: %d opened, %d closed for %s",
            created_count, closed_count, target_date,
        )
        return {"created": created_count, "updated": closed_count}


class DailySegmentStatsAggregator:
    """Segment active snapshots by zip_code, bedrooms, property_type, portfolio, price_band."""

    def run(self, target_date):
        snapshots = DailyUnitSnapshot.objects.filter(
            snapshot_date=target_date, status="active"
        ).select_related("unit__property__portfolio")

        # Leasing summaries for this date, indexed by unit_id
        leasing_by_unit = {
            s.unit_id: s
            for s in DailyLeasingSummary.objects.filter(summary_date=target_date)
        }

        # Build segment buckets
        segments = defaultdict(list)
        for snap in snapshots:
            unit = snap.unit
            prop = unit.property

            zip_code = unit.postal_code or prop.postal_code
            if zip_code:
                segments[("zip_code", zip_code)].append(snap)

            if snap.bedrooms is not None:
                segments[("bedrooms", str(snap.bedrooms))].append(snap)

            if prop.property_type:
                segments[("property_type", prop.property_type)].append(snap)

            if prop.portfolio:
                segments[("portfolio", prop.portfolio.name)].append(snap)

            band = _price_band(snap.listed_price)
            segments[("price_band", band)].append(snap)

        created_count = 0
        updated_count = 0

        for (seg_type, seg_value), snaps in segments.items():
            count = len(snaps)
            avg_dom = sum(s.days_on_market or 0 for s in snaps) / count if count else 0
            avg_price = sum(s.listed_price or 0 for s in snaps) / count if count else 0
            count_30_plus = sum(1 for s in snaps if (s.days_on_market or 0) >= 30)

            # Sum leasing activity for units in this segment
            unit_ids = {s.unit_id for s in snaps}
            leads = sum(leasing_by_unit[uid].leads_count for uid in unit_ids if uid in leasing_by_unit)
            showings = sum(leasing_by_unit[uid].showings_completed_count for uid in unit_ids if uid in leasing_by_unit)
            apps = sum(leasing_by_unit[uid].applications_count for uid in unit_ids if uid in leasing_by_unit)

            _, was_created = DailySegmentStats.objects.update_or_create(
                snapshot_date=target_date,
                segment_type=seg_type,
                segment_value=seg_value,
                defaults={
                    "active_unit_count": count,
                    "average_dom": int(avg_dom),
                    "average_price": Decimal(str(round(float(avg_price), 2))),
                    "count_30_plus_dom": count_30_plus,
                    "leads_count": leads,
                    "showings_count": showings,
                    "applications_count": apps,
                    "lead_to_show_rate": _safe_ratio(showings, leads),
                    "show_to_app_rate": _safe_ratio(apps, showings),
                },
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

        logger.info(
            "DailySegmentStats: %d created, %d updated for %s",
            created_count, updated_count, target_date,
        )
        return {"created": created_count, "updated": updated_count}


class WeeklyLeasingSummaryAggregator:
    """Aggregate DailyLeasingSummary into weekly summaries per unit."""

    def run(self, week_ending):
        """week_ending is a Sunday. Aggregates Mon-Sun."""
        week_start = week_ending - timedelta(days=6)

        daily = DailyLeasingSummary.objects.filter(
            summary_date__gte=week_start,
            summary_date__lte=week_ending,
        ).values("unit_id", "unit__property__address_line_1").annotate(
            total_leads=Sum("leads_count"),
            total_showings=Sum("showings_completed_count"),
            total_missed=Sum("showings_missed_count"),
            total_apps=Sum("applications_count"),
        )

        created_count = 0
        updated_count = 0

        for row in daily:
            leads = row["total_leads"] or 0
            showings = row["total_showings"] or 0
            missed = row["total_missed"] or 0
            apps = row["total_apps"] or 0

            _, was_created = WeeklyLeasingSummary.objects.update_or_create(
                week_ending=week_ending,
                unit_id=row["unit_id"],
                defaults={
                    "leads_count": leads,
                    "showings_completed_count": showings,
                    "showings_missed_count": missed,
                    "applications_count": apps,
                    "lead_to_show_rate": _safe_ratio(showings, leads),
                    "show_to_app_rate": _safe_ratio(apps, showings),
                    "property_display_name": row["unit__property__address_line_1"] or "",
                },
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

        logger.info(
            "WeeklyLeasingSummary: %d created, %d updated for week ending %s",
            created_count, updated_count, week_ending,
        )
        return {"created": created_count, "updated": updated_count}


class MonthlyMarketReportAggregator:
    """Aggregate DailyMarketStats + DailyLeasingSummary into monthly report."""

    def run(self, year, month):
        first_day = date(year, month, 1)

        # Average daily market stats for the month
        market_agg = DailyMarketStats.objects.filter(
            snapshot_date__year=year,
            snapshot_date__month=month,
        ).aggregate(
            avg_dom=Avg("average_dom"),
            avg_price=Avg("average_price"),
            avg_30_plus=Avg("count_30_plus_dom"),
        )

        # Sum leasing activity for the month
        leasing_agg = DailyLeasingSummary.objects.filter(
            summary_date__year=year,
            summary_date__month=month,
        ).aggregate(
            total_leads=Sum("leads_count"),
            total_showings=Sum("showings_completed_count"),
            total_missed=Sum("showings_missed_count"),
            total_apps=Sum("applications_count"),
        )

        leads = leasing_agg["total_leads"] or 0
        showings = leasing_agg["total_showings"] or 0
        missed = leasing_agg["total_missed"] or 0
        apps = leasing_agg["total_apps"] or 0

        _, created = MonthlyMarketReport.objects.update_or_create(
            report_month=first_day,
            defaults={
                "average_dom": int(market_agg["avg_dom"] or 0),
                "average_price": market_agg["avg_price"] or Decimal("0"),
                "average_30_plus_dom_count": Decimal(str(round(float(market_agg["avg_30_plus"] or 0), 1))),
                "total_leads": leads,
                "total_showings": showings,
                "total_missed_showings": missed,
                "total_applications": apps,
                "lead_to_show_rate": _safe_ratio(showings, leads),
                "show_to_app_rate": _safe_ratio(apps, showings),
            },
        )
        action = "created" if created else "updated"
        logger.info("MonthlyMarketReport %s for %s-%02d", action, year, month)
        return {"created": int(created), "updated": int(not created)}


class MonthlySegmentStatsAggregator:
    """Aggregate monthly stats by (zip_code, bedroom_count) for the rental index."""

    def run(self, year, month):
        first_day = date(year, month, 1)

        # Active snapshots for the month, grouped by unit
        snapshots = DailyUnitSnapshot.objects.filter(
            snapshot_date__year=year,
            snapshot_date__month=month,
            status="active",
        ).select_related("unit__property")

        # Build (zip, bedrooms) buckets
        buckets = defaultdict(lambda: {
            "prices": [],
            "doms": [],
            "unit_ids": set(),
        })
        for snap in snapshots:
            unit = snap.unit
            zip_code = unit.postal_code or unit.property.postal_code
            bedrooms = snap.bedrooms
            if not zip_code or bedrooms is None:
                continue

            key = (zip_code, bedrooms)
            buckets[key]["prices"].append(float(snap.listed_price or 0))
            buckets[key]["doms"].append(snap.days_on_market or 0)
            buckets[key]["unit_ids"].add(unit.id)

        # Leasing summaries for the month
        leasing = DailyLeasingSummary.objects.filter(
            summary_date__year=year,
            summary_date__month=month,
        ).values("unit_id").annotate(
            total_leads=Sum("leads_count"),
            total_showings=Sum("showings_completed_count"),
            total_apps=Sum("applications_count"),
        )
        leasing_by_unit = {r["unit_id"]: r for r in leasing}

        # Occupancy counts: snapshots with status "occupied" for the month
        occupied_units = DailyUnitSnapshot.objects.filter(
            snapshot_date__year=year,
            snapshot_date__month=month,
            status="occupied",
        ).values("unit__postal_code", "unit__property__postal_code", "bedrooms").annotate(
            occupied_count=Count("unit_id", distinct=True),
        )
        occupied_map = {}
        for row in occupied_units:
            zip_code = row["unit__postal_code"] or row["unit__property__postal_code"]
            if zip_code and row["bedrooms"] is not None:
                occupied_map[(zip_code, row["bedrooms"])] = row["occupied_count"]

        created_count = 0
        updated_count = 0

        for (zip_code, bedrooms), data in buckets.items():
            unit_ids = data["unit_ids"]
            prices = data["prices"]
            doms = data["doms"]

            avg_list_price = Decimal(str(round(sum(prices) / len(prices), 2))) if prices else Decimal("0")
            avg_dom = int(sum(doms) / len(doms)) if doms else 0

            # Avg occupied rent from active leases in this segment
            rent_agg = Lease.objects.filter(
                primary_lease_status=2,
                rent_amount__gt=0,
                unit__postal_code=zip_code,
                unit__bedrooms=bedrooms,
            ).aggregate(avg_rent=Avg("rent_amount"))

            # Leases written this month in this segment
            leases_written = Lease.objects.filter(
                start_date__year=year,
                start_date__month=month,
                unit__postal_code=zip_code,
                unit__bedrooms=bedrooms,
            ).count()

            # Average lease length for leases in this segment
            lease_length_agg = Lease.objects.filter(
                unit__postal_code=zip_code,
                unit__bedrooms=bedrooms,
                start_date__isnull=False,
                end_date__isnull=False,
            ).annotate(
                length_days=F("end_date") - F("start_date"),
            ).aggregate(avg_length=Avg("length_days"))
            avg_lease_months = Decimal("0")
            if lease_length_agg["avg_length"]:
                avg_days = lease_length_agg["avg_length"]
                if hasattr(avg_days, "days"):
                    avg_days = avg_days.days
                avg_lease_months = Decimal(str(round(float(avg_days) / 30.44, 1)))

            # Sum leasing activity for units in this segment
            leads = sum(leasing_by_unit.get(uid, {}).get("total_leads", 0) for uid in unit_ids)
            showings = sum(leasing_by_unit.get(uid, {}).get("total_showings", 0) for uid in unit_ids)
            apps = sum(leasing_by_unit.get(uid, {}).get("total_apps", 0) for uid in unit_ids)

            occupied = occupied_map.get((zip_code, bedrooms), 0)
            vacant = len(unit_ids)

            _, was_created = MonthlySegmentStats.objects.update_or_create(
                month=first_day,
                zip_code=zip_code,
                bedroom_count=bedrooms,
                defaults={
                    "avg_occupied_rent": rent_agg["avg_rent"] or Decimal("0"),
                    "avg_list_price": avg_list_price,
                    "avg_dom": avg_dom,
                    "avg_lease_length_months": avg_lease_months,
                    "leases_written_count": leases_written,
                    "total_leads": leads,
                    "total_showings": showings,
                    "total_applications": apps,
                    "occupied_unit_count": occupied,
                    "vacant_unit_count": vacant,
                },
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

        logger.info(
            "MonthlySegmentStats: %d created, %d updated for %s-%02d",
            created_count, updated_count, year, month,
        )
        return {"created": created_count, "updated": updated_count}
