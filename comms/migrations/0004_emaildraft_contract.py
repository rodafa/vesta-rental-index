"""
Contract migration: remove legacy week_start/week_end fields from EmailDraft.

Deploy 1 (0003) added period_type/period_start/period_end alongside week_*.
All reads now use period_*. This migration:
  1. Defensive backfill — catch any rows written by old code during the drain window.
  2. Make period_start/period_end non-nullable.
  3. Remove the legacy unique_draft_per_owner_per_week constraint.
  4. Remove week_start and week_end columns.

Reverse limitation: the reverse RunPython re-adds week_start/week_end as nullable
columns and backfills from period_*, but cannot restore the NOT NULL constraint
or the old unique constraint automatically. A manual follow-up would be needed
after a rollback to restore the full 0003 schema.
"""

from django.db import migrations, models


def defensive_backfill(apps, schema_editor):
    """
    Catch any row where period_start or period_end is NULL — these would have
    been written by Deploy-1 code that only dual-wrote week_*. Copy week_*
    values into period_* and set period_type="weekly".
    """
    EmailDraft = apps.get_model("comms", "EmailDraft")
    EmailDraft.objects.filter(
        models.Q(period_start__isnull=True) | models.Q(period_end__isnull=True)
    ).update(
        period_type="weekly",
        period_start=models.F("week_start"),
        period_end=models.F("week_end"),
    )


def reverse_defensive_backfill(apps, schema_editor):
    """
    Reverse is a no-op for the backfill data — period_* already contains
    the correct values. The structural reverse (re-adding week_* columns)
    is handled by the RemoveField reverse operations.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0003_emaildraft_period_fields"),
    ]

    operations = [
        # 1. Defensive backfill for drain-window rows
        migrations.RunPython(defensive_backfill, reverse_defensive_backfill),
        # 2. Make period_start and period_end non-nullable
        migrations.AlterField(
            model_name="emaildraft",
            name="period_start",
            field=models.DateField(),
        ),
        migrations.AlterField(
            model_name="emaildraft",
            name="period_end",
            field=models.DateField(),
        ),
        # 3. Remove the legacy constraint
        migrations.RemoveConstraint(
            model_name="emaildraft",
            name="unique_draft_per_owner_per_week",
        ),
        # 4. Remove legacy columns
        migrations.RemoveField(
            model_name="emaildraft",
            name="week_start",
        ),
        migrations.RemoveField(
            model_name="emaildraft",
            name="week_end",
        ),
    ]
