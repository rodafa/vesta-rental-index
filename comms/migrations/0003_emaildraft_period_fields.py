"""
Expand migration: add period_type, period_start, period_end to EmailDraft.

This is the EXPAND half of an expand/contract migration. It adds new fields
and a new constraint but does NOT remove the old week_start/week_end fields
or the old unique_draft_per_owner_per_week constraint. The contract migration
(dropping old fields) ships in a later deploy after prod verification.
"""

from django.db import migrations, models


def backfill_period_fields(apps, schema_editor):
    """Copy week_start/week_end into period_start/period_end for all existing rows."""
    EmailDraft = apps.get_model("comms", "EmailDraft")
    EmailDraft.objects.filter(period_start__isnull=True).update(
        period_type="weekly",
        period_start=models.F("week_start"),
        period_end=models.F("week_end"),
    )


def reverse_backfill(apps, schema_editor):
    """Copy period_start/period_end back to week_start/week_end (for rollback)."""
    EmailDraft = apps.get_model("comms", "EmailDraft")
    EmailDraft.objects.filter(period_start__isnull=False).update(
        week_start=models.F("period_start"),
        week_end=models.F("period_end"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0002_remove_emaildraft_comms_email_owner_i_8c607c_idx_and_more"),
    ]

    operations = [
        # 1. Add the three new fields (all nullable / with defaults)
        migrations.AddField(
            model_name="emaildraft",
            name="period_type",
            field=models.CharField(
                choices=[("weekly", "Weekly"), ("monthly", "Monthly")],
                db_index=True,
                default="weekly",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="emaildraft",
            name="period_start",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="emaildraft",
            name="period_end",
            field=models.DateField(blank=True, null=True),
        ),
        # 2. Backfill existing rows
        migrations.RunPython(backfill_period_fields, reverse_backfill),
        # 3. Add the new unique constraint
        migrations.AddConstraint(
            model_name="emaildraft",
            constraint=models.UniqueConstraint(
                fields=["product", "owner", "period_type", "period_start"],
                name="unique_draft_per_owner_per_period",
            ),
        ),
    ]
