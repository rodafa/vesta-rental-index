"""
Management command: extract utility proposals from Rentvine notes and listings.

Thin orchestrator — all logic lives in utilities.services.
"""

import logging

import anthropic
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from integrations.rentvine.client import RentvineClient, RentvineAPIError
from utilities.models import ExtractionRun
from utilities.services import (
    extract_property_utilities,
    fetch_property_address,
)

logger = logging.getLogger(__name__)

MAX_PROPERTIES = 10


class Command(BaseCommand):
    help = (
        "Extract utility proposals from Rentvine notes and listing "
        "descriptions for specified properties."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--property-ids",
            type=str,
            required=True,
            help=(
                "Comma-separated Rentvine property IDs (required, max "
                f"{MAX_PROPERTIES})."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Make model calls but write nothing to the database. "
                "Print a per-unit table of what would be saved."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # -- Parse and validate property IDs --
        raw_ids = [s.strip() for s in options["property_ids"].split(",") if s.strip()]
        try:
            property_ids = [int(x) for x in raw_ids]
        except ValueError:
            raise CommandError("All --property-ids values must be integers.")

        if not property_ids:
            raise CommandError("At least one property ID is required.")

        if len(property_ids) > MAX_PROPERTIES:
            raise CommandError(
                f"Too many IDs ({len(property_ids)}). Maximum is {MAX_PROPERTIES}."
            )

        # -- Initialize clients --
        rv = RentvineClient()
        ai = anthropic.Anthropic()
        model_name = getattr(settings, "ANTHROPIC_MODEL", "claude-sonnet-4-6")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no database writes.\n"))

        # -- Resolve addresses (before creating ExtractionRun so a
        #    RentvineAPIError here never leaves a run stuck in "running") --
        addresses = {}
        for pid in property_ids:
            addresses[pid] = fetch_property_address(rv, pid)
            # fetch_property_address lets RentvineAPIError propagate;
            # no run exists yet, so nothing to finalize.

        # -- Create ExtractionRun (skip in dry_run) --
        run = None
        if not dry_run:
            run = ExtractionRun.objects.create(
                started_at=timezone.now(),
                status="running",
                model_name=model_name,
                scope=f"property_ids={','.join(str(p) for p in property_ids)}",
            )
            logger.info(
                "ExtractionRun created",
                extra={"run_id": run.pk, "property_ids": property_ids},
            )

        # -- Process each property --
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "notes_sent": 0,
            "notes_dropped": 0,
            "lines_redacted": 0,
            "created": 0,
            "skipped": 0,
            "quote_failed": 0,
            "invalid_ref": 0,
            "invalid_field": 0,
            "properties_ok": 0,
            "properties_skipped": 0,
            "properties_failed": 0,
        }
        failed_property_ids = []
        all_dry_run_proposals = []
        rentvine_fatal = False

        try:
            for pid in property_ids:
                addr = addresses[pid]
                self.stdout.write(f"\nProperty {pid}: {addr}")
                self.stdout.write("-" * 60)

                try:
                    result = extract_property_utilities(
                        rv_client=rv,
                        ai_client=ai,
                        model_name=model_name,
                        property_id=pid,
                        address=addr,
                        dry_run=dry_run,
                        run=run,
                    )
                except RentvineAPIError as exc:
                    # Rentvine error (including 429 after retries) → stop whole run
                    self.stdout.write(self.style.ERROR(
                        f"  Rentvine error: {exc}"
                    ))
                    logger.error(
                        "Rentvine error, stopping run",
                        extra={"property_id": pid, "error": str(exc)},
                    )
                    failed_property_ids.append(pid)
                    totals["properties_failed"] += 1
                    rentvine_fatal = True
                    break

                # Accumulate tokens regardless of success/failure
                totals["input_tokens"] += result["input_tokens"]
                totals["output_tokens"] += result["output_tokens"]
                totals["notes_sent"] += result["notes_sent"]
                totals["notes_dropped"] += result["notes_dropped"]
                totals["lines_redacted"] += result["lines_redacted"]
                totals["quote_failed"] += result["quote_failed"]
                totals["invalid_ref"] += result["invalid_ref"]
                totals["invalid_field"] += result["invalid_field"]

                if result["error"]:
                    # Model call failed for this property — skip, continue
                    self.stdout.write(self.style.ERROR(
                        f"  FAILED: {result['error']}"
                    ))
                    if result.get("raw_reply_snippet"):
                        self.stdout.write(
                            f"  Reply: {result['raw_reply_snippet']}"
                        )
                    failed_property_ids.append(pid)
                    totals["properties_failed"] += 1
                    continue

                if result["active_units"] == 0:
                    self.stdout.write("  No active units, skipped.")
                    totals["properties_skipped"] += 1
                    continue

                totals["created"] += result["created"]
                totals["skipped"] += result["skipped"]
                totals["properties_ok"] += 1

                # -- Per-property output --
                self.stdout.write(
                    f"  Units: {result['active_units']} active"
                )
                self.stdout.write(
                    f"  Notes: {result['notes_sent']} sent, "
                    f"{result['notes_dropped']} dropped"
                )
                if result["lines_redacted"]:
                    self.stdout.write(
                        f"  Lines removed (security): "
                        f"{result['lines_redacted']}"
                    )
                self.stdout.write(
                    f"  Tokens: {result['input_tokens']} in, "
                    f"{result['output_tokens']} out"
                )

                if dry_run:
                    self._print_dry_run_table(result["proposals"], pid)
                    all_dry_run_proposals.extend(result["proposals"])
                else:
                    self.stdout.write(
                        f"  Proposals: {result['created']} created, "
                        f"{result['skipped']} skipped (existing)"
                    )
                    if result["quote_failed"]:
                        self.stdout.write(
                            f"  Rows dropped: {result['quote_failed']} quote failed, "
                            f"{result['invalid_ref']} invalid ref"
                        )

        except Exception as exc:
            # Unexpected error — finalize run before re-raising
            if run is not None:
                run.finished_at = timezone.now()
                run.status = "failed"
                run.input_tokens = totals["input_tokens"]
                run.output_tokens = totals["output_tokens"]
                run.notes_sent = totals["notes_sent"]
                run.notes_dropped = totals["notes_dropped"]
                run.error_text = (
                    f"Unexpected {type(exc).__name__} during extraction"
                )
                if failed_property_ids:
                    run.error_text += (
                        f"; failed properties: "
                        f"{','.join(str(p) for p in failed_property_ids)}"
                    )
                run.save()
            raise

        # -- Finalize ExtractionRun (normal path) --
        if run is not None:
            run.finished_at = timezone.now()
            run.input_tokens = totals["input_tokens"]
            run.output_tokens = totals["output_tokens"]
            run.notes_sent = totals["notes_sent"]
            run.notes_dropped = totals["notes_dropped"]

            if failed_property_ids:
                run.status = "failed"
                run.error_text = (
                    f"Failed properties: {','.join(str(p) for p in failed_property_ids)}"
                )
                if rentvine_fatal:
                    run.error_text += " (Rentvine error, run stopped)"
            else:
                run.status = "succeeded"

            run.save()

        # -- Summary --
        self.stdout.write(f"\n{'=' * 60}")
        self.stdout.write("Summary:")
        self.stdout.write(
            f"  Properties: {totals['properties_ok']} ok, "
            f"{totals['properties_skipped']} skipped, "
            f"{totals['properties_failed']} failed"
        )
        if dry_run:
            self.stdout.write(
                f"  Proposals: {len(all_dry_run_proposals)} would be created"
            )
        else:
            self.stdout.write(
                f"  Proposals: {totals['created']} created, "
                f"{totals['skipped']} skipped (existing)"
            )
        self.stdout.write(
            f"  Tokens: {totals['input_tokens']} input, "
            f"{totals['output_tokens']} output"
        )
        self.stdout.write(
            f"  Notes: {totals['notes_sent']} sent, "
            f"{totals['notes_dropped']} dropped"
        )
        if totals["lines_redacted"]:
            self.stdout.write(
                f"  Lines removed (security): {totals['lines_redacted']}"
            )
        if totals["quote_failed"] or totals["invalid_ref"] or totals["invalid_field"]:
            self.stdout.write(
                f"  Rows dropped: {totals['quote_failed']} quote failed, "
                f"{totals['invalid_ref']} invalid ref, "
                f"{totals['invalid_field']} invalid field"
            )

    def _print_dry_run_table(self, proposals: list[dict], property_id: int):
        """Print a formatted table of proposals for dry-run output."""
        if not proposals:
            self.stdout.write("  (no proposals)")
            return

        self.stdout.write("")
        # Group by unit for readability
        current_unit = None
        conflict_notes = []

        for p in sorted(proposals, key=lambda x: (x["rentvine_unit_id"], x["utility_type"])):
            uid = p["rentvine_unit_id"]
            if uid != current_unit:
                current_unit = uid

            # Truncate quote for display
            quote = p["evidence_quote"]
            if len(quote) > 40:
                quote = quote[:37] + "..."
            quote = quote.replace("\n", "\\n")

            conflict_flag = " CONFLICT" if p["has_conflict"] else ""

            self.stdout.write(
                f"  Unit {uid:<6}| {p['utility_type']:<12}| "
                f"{p['responsibility']:<17}| {p['provider'] or '':<15}| "
                f"{p['basis']:<9}| \"{quote}\"{conflict_flag}"
            )

            # Show extras for oil/propane
            if p["utility_type"] in ("oil", "propane") and p["extras"]:
                non_null = {k: v for k, v in p["extras"].items() if v is not None}
                if non_null:
                    self.stdout.write(
                        f"           extras: {non_null}"
                    )

            if p["has_conflict"] and p["conflict_note"] not in conflict_notes:
                conflict_notes.append(p["conflict_note"])

        if conflict_notes:
            self.stdout.write("\n  Conflicts:")
            for note in conflict_notes:
                self.stdout.write(f"    {note}")
