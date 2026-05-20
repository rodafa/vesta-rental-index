"""
Weekly update orchestrator.

Iterates over actively-listed units, gathers metrics, drafts AI notes via Claude,
and persists to PropertyWeeklyNote.
"""

import logging
from datetime import date, timedelta

from django.utils import timezone

from weekly_reports.models import WeeklyReportRun

logger = logging.getLogger(__name__)


def default_date_range():
    """Return (start_date, end_date) for the most recent Tue-Mon week.

    start_date = most recent Tuesday (excluding today)
    end_date = the Monday after that Tuesday (start + 6 days)
    """
    today = date.today()
    days_since_tuesday = (today.weekday() - 1) % 7
    if days_since_tuesday == 0:
        days_since_tuesday = 7  # exclude today
    start = today - timedelta(days=days_since_tuesday)
    end = start + timedelta(days=6)
    return start, end


def run_for_week(start_date, end_date, dry_run=False, skip_claude=False,
                 property_filter=None, triggered_by="", run=None):
    """Execute the weekly update pipeline.

    Args:
        start_date: Inclusive start date of the reporting period.
        end_date: Inclusive end date of the reporting period.
        dry_run: Run the full pipeline but skip writes (Claude + DB).
        skip_claude: Skip Claude API calls (useful with dry_run to save cost).
        property_filter: Optional address substring to scope to one property.
        triggered_by: Who triggered this run (e.g. "cli", "api:username").
        run: Optional pre-created WeeklyReportRun (avoids double-create from API).

    Returns:
        WeeklyReportRun instance with results.
    """
    from dashboard.models import PropertyWeeklyNote
    from properties.models import Unit
    from reports.services.data_sources import leadsimple as ls_source

    from weekly_reports.services import (
        note_drafter,
        slack_summary,
        unit_data,
    )

    if run is None:
        run = WeeklyReportRun.objects.create(
            start_date=start_date,
            end_date=end_date,
            dry_run=dry_run,
            triggered_by=triggered_by,
        )

    try:
        # Step 1: Resolve the set of units to process
        all_units = list(
            Unit.objects.filter(is_active=True).select_related("property")
        )
        snapshots, _ = unit_data.get_snapshot_data([u.id for u in all_units])
        listed_units = [
            u for u in all_units
            if snapshots.get(u.id, {}).get("status") == "active"
        ]

        if property_filter:
            listed_units = [
                u for u in listed_units
                if property_filter.lower() in (
                    u.address_line_1 or (u.property.address_line_1 if u.property else "")
                ).lower()
            ]
            logger.info("Filtered to %d units matching '%s'", len(listed_units), property_filter)

        logger.info("Found %d actively-listed units", len(listed_units))

        # Step 2: Portfolio benchmark
        listed_ids = [u.id for u in listed_units]
        benchmark = unit_data.get_portfolio_benchmark(listed_ids, start_date, end_date)

        # Step 3: Weekly metrics for all listed units
        metrics = unit_data.get_weekly_metrics(listed_ids, start_date, end_date)

        # Step 4: Fetch LeadSimple processes once
        all_deals = ls_source.fetch_all_deals(include_closed_since=start_date)

        # Step 5: History for prompt context
        week_date_for_notes = start_date - timedelta(days=start_date.weekday())
        history_map = unit_data.get_recent_history(listed_ids, week_date_for_notes)

        # Step 6: Process each unit
        errors = []

        for unit in listed_units:
            run.units_processed += 1

            try:
                snap = snapshots.get(unit.id, {})
                dom = snap.get("days_on_market") or 0
                list_price = snap.get("listed_price") or 0
                wk = metrics.get(unit.id, {})
                leads = wk.get("leads", 0)
                showings = wk.get("showings", 0)
                apps = wk.get("apps", 0)

                address = (
                    unit.address_line_1
                    or (unit.property.address_line_1 if unit.property else "")
                    or f"Unit #{unit.id}"
                )

                # Skip if no activity data at all
                if not wk and dom == 0:
                    run.units_skipped += 1
                    logger.info("Skipping unit %s (%s): no data in window", unit.id, address)
                    continue

                # Showing feedback
                feedback_texts = unit_data.get_showing_feedback_for_unit(
                    unit.id, start_date, end_date
                )
                feedback_text = "\n".join(feedback_texts) if feedback_texts else ""

                # LeadSimple context
                prop_address = (
                    unit.property.address_line_1 if unit.property else ""
                ) or unit.address_line_1 or ""

                active_deals = ls_source.match_deals_to_address(prop_address, [
                    d for d in all_deals if not d.get("closed_at")
                ])
                resolved_deals = ls_source.get_resolved_for_property(
                    unit.property or unit, all_deals, start_date, end_date
                )
                move_in_deals = [
                    d for d in active_deals if d.get("pipeline_type") == "move_in"
                ]

                # History
                history = history_map.get(unit.id, [])

                # Build note input
                note_input = {
                    "address": address,
                    "beds": unit.bedrooms or 0,
                    "baths": (unit.full_bathrooms or 0) + (unit.half_bathrooms or 0) * 0.5,
                    "list_price": int(list_price) if list_price else 0,
                    "dom": dom,
                    "leads": leads,
                    "showings": showings,
                    "apps": apps,
                    "feedback_text": feedback_text,
                    "active_deals": active_deals,
                    "resolved_deals": resolved_deals,
                    "move_ins": move_in_deals,
                    "history": history,
                    "benchmark": benchmark,
                }

                # Build prompt (always — validates data pipeline even in dry-run)
                note_drafter.build_prompt(note_input)

                if dry_run:
                    logger.info(
                        "[DRY RUN] Unit %s (%s): leads=%s showings=%s apps=%s DOM=%s price=%s — would write note",
                        unit.id, address, leads, showings, apps, dom,
                        int(list_price) if list_price else 0,
                    )
                    run.notes_drafted += 1
                    continue

                # Draft note via Claude
                note_text = None
                if not skip_claude:
                    note_text = note_drafter.draft_note(note_input)
                else:
                    logger.info("Unit %s: Claude call skipped (--skip-claude)", unit.id)

                if not note_text:
                    run.units_skipped += 1
                    continue

                # Persist — update if draft, skip if approved
                existing = PropertyWeeklyNote.objects.filter(
                    unit_id=unit.id, week_date=week_date_for_notes
                ).first()

                if existing and getattr(existing, "approved", False):
                    run.units_skipped += 1
                    logger.info(
                        "Skipping unit %s (%s): approved note already exists",
                        unit.id, address,
                    )
                    continue

                defaults = {
                    "note_text": note_text,
                    "author": "Weekly Update",
                }
                # Set approval fields if they exist on the model (added in Phase 2)
                if hasattr(PropertyWeeklyNote, "approved"):
                    defaults["approved"] = False
                    defaults["approved_at"] = None
                    defaults["approved_by"] = ""

                PropertyWeeklyNote.objects.update_or_create(
                    unit_id=unit.id,
                    week_date=week_date_for_notes,
                    defaults=defaults,
                )
                run.notes_drafted += 1
                logger.info("Drafted note for unit %s (%s)", unit.id, address)

            except Exception as exc:
                logger.exception("Error processing unit %s", unit.id)
                errors.append({"unit": str(unit.id), "error": str(exc)})
                run.units_errored += 1

        # Step 7: Finalize run
        run.error_log = errors

        if run.units_errored == 0:
            run.status = "success"
        elif run.notes_drafted > 0:
            run.status = "partial"
        else:
            run.status = "failed"

        run.completed_at = timezone.now()
        run.save()

        # Slack summary (skip in dry_run)
        if not dry_run:
            try:
                slack_summary.post_run_summary(run)
            except Exception:
                logger.exception("Failed to post Slack summary")

        logger.info(
            "Weekly update complete: processed=%d drafted=%d skipped=%d errors=%d",
            run.units_processed, run.notes_drafted, run.units_skipped, run.units_errored,
        )

    except Exception as exc:
        logger.exception("Weekly update failed")
        run.status = "failed"
        run.error_log = [{"error": str(exc)}]
        run.completed_at = timezone.now()
        run.save()

    return run
