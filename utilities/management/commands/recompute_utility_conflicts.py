"""
Management command: recompute conflict flags for pending utility proposals.

Thin orchestrator -- all decision logic lives in
utilities.services.recompute_conflict_for_group.
"""

import logging
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from utilities.models import UtilityProposal
from utilities.services import recompute_conflict_for_group

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Recompute has_conflict and conflict_note for all pending proposals "
        "using the updated conflict_class rule. Dry-run by default."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            default=False,
            help="Actually write changes. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLY" if apply else "DRY RUN"
        self.stdout.write(f"Mode: {mode}")

        # Fetch all pending proposals
        pending = list(
            UtilityProposal.objects.filter(status="pending").values(
                "id", "rentvine_unit_id", "utility_type",
                "responsibility", "has_conflict", "conflict_note",
            )
        )
        total = len(pending)
        flagged_before = sum(1 for p in pending if p["has_conflict"])

        self.stdout.write(f"Pending rows total: {total}")
        self.stdout.write(f"Rows flagged before: {flagged_before}")

        # Group by (rentvine_unit_id, utility_type)
        groups = defaultdict(list)
        for p in pending:
            groups[(p["rentvine_unit_id"], p["utility_type"])].append(p)

        # Recompute each group
        updates = []  # (pk, new_has_conflict, new_conflict_note)
        for key, group in groups.items():
            results = recompute_conflict_for_group(group)
            for pk, new_flag, new_note in results:
                # Find the original row to compare
                original = next(p for p in group if p["id"] == pk)
                old_flag = original["has_conflict"]
                old_note = original.get("conflict_note") or ""
                if new_flag != old_flag or new_note != old_note:
                    updates.append((pk, new_flag, new_note, old_note))

        # Compute stats
        flagged_after_count = 0
        cleared = []
        newly_flagged = []
        for pk, new_flag, new_note, old_note in updates:
            original = next(p for p in pending if p["id"] == pk)
            if original["has_conflict"] and not new_flag:
                cleared.append((pk, original["rentvine_unit_id"],
                                original["utility_type"], old_note))
            elif not original["has_conflict"] and new_flag:
                newly_flagged.append((pk, original["rentvine_unit_id"],
                                      original["utility_type"], new_note))

        # Count total flagged after (including unchanged)
        update_pks = {pk for pk, _, _, _ in updates}
        for p in pending:
            if p["id"] in update_pks:
                new_flag = next(nf for pk, nf, _, _ in updates if pk == p["id"])
                if new_flag:
                    flagged_after_count += 1
            else:
                if p["has_conflict"]:
                    flagged_after_count += 1

        self.stdout.write(f"Rows flagged after: {flagged_after_count}")
        self.stdout.write(f"Rows cleared: {len(cleared)}")
        self.stdout.write(f"Rows newly flagged: {len(newly_flagged)}")

        if newly_flagged:
            self.stderr.write(
                f"WARNING: {len(newly_flagged)} rows newly flagged "
                f"(expected 0). Investigate before applying."
            )
            for pk, uid, utype, note in newly_flagged[:10]:
                self.stderr.write(f"  pk={pk} unit={uid} type={utype} note={note}")

        # Sample cleared rows
        self.stdout.write(f"\nSample cleared rows (up to 10):")
        for pk, uid, utype, old_note in cleared[:10]:
            self.stdout.write(f"  unit={uid} type={utype} old_note={old_note}")

        if not cleared and not newly_flagged:
            self.stdout.write("  (none)")

        # Apply if requested
        if apply and updates:
            objs = {
                p.pk: p
                for p in UtilityProposal.objects.filter(
                    pk__in=[pk for pk, _, _, _ in updates]
                )
            }
            to_save = []
            for pk, new_flag, new_note, _ in updates:
                obj = objs[pk]
                obj.has_conflict = new_flag
                obj.conflict_note = new_note
                to_save.append(obj)

            with transaction.atomic():
                UtilityProposal.objects.bulk_update(
                    to_save, ["has_conflict", "conflict_note"],
                )

            self.stdout.write(f"\nWrote {len(to_save)} rows.")
            logger.info(
                "recompute_utility_conflicts applied",
                extra={
                    "total": total,
                    "flagged_before": flagged_before,
                    "flagged_after": flagged_after_count,
                    "cleared": len(cleared),
                    "newly_flagged": len(newly_flagged),
                    "updated": len(to_save),
                },
            )
        elif apply:
            self.stdout.write("\nNo changes to write.")
        else:
            self.stdout.write(f"\nDry run complete. Re-run with --apply to write.")
