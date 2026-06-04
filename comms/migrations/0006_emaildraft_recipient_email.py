"""
Add recipient_email to EmailDraft and update UniqueConstraint.

recipient_email is a normalized (lowered, stripped) email address of the
intended recipient. Backfills existing rows from owner.email.

UniqueConstraint changes from (product, owner, period_type, period_start)
to (product, recipient_email, period_type, period_start) — prevents
double-send to one email address per period.
"""

from django.db import migrations, models


def backfill_recipient_email(apps, schema_editor):
    """Backfill recipient_email from owner.email for existing drafts."""
    EmailDraft = apps.get_model("comms", "EmailDraft")
    for draft in EmailDraft.objects.select_related("owner").all():
        email = (draft.owner.email or "").strip().lower()
        draft.recipient_email = email
        draft.save(update_fields=["recipient_email"])


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0005_emaildraft_approved_generated_note"),
    ]

    operations = [
        # 1. Add the field with a default
        migrations.AddField(
            model_name="emaildraft",
            name="recipient_email",
            field=models.CharField(
                db_index=True,
                default="",
                help_text="Normalized (lowered, stripped) email address of the intended recipient.",
                max_length=254,
            ),
        ),
        # 2. Backfill from owner.email
        migrations.RunPython(backfill_recipient_email, migrations.RunPython.noop),
        # 3. Remove old constraint
        migrations.RemoveConstraint(
            model_name="emaildraft",
            name="unique_draft_per_owner_per_period",
        ),
        # 4. Add new constraint
        migrations.AddConstraint(
            model_name="emaildraft",
            constraint=models.UniqueConstraint(
                fields=["product", "recipient_email", "period_type", "period_start"],
                name="unique_draft_per_email_per_period",
            ),
        ),
    ]
