"""
Add recipient_email to EmailDraft with a partial unique constraint scoped
to product="monthly_owner_notes".

The existing unique_draft_per_owner_per_period constraint is left in place —
it governs the maintenance product and must not change.

The new partial constraint prevents double-send to one email address per
period, but ONLY for monthly_owner_notes rows with a non-blank email.
"""

from django.db import migrations, models


def backfill_and_dedupe(apps, schema_editor):
    """
    1. Backfill recipient_email from owner.email for ALL drafts.
    2. Dedupe monthly_owner_notes rows that would collide under the new
       partial constraint — keep the most recently created, delete the rest.
    """
    EmailDraft = apps.get_model("comms", "EmailDraft")

    # Backfill all rows
    for draft in EmailDraft.objects.select_related("owner").all():
        email = (draft.owner.email or "").strip().lower()
        draft.recipient_email = email
        draft.save(update_fields=["recipient_email"])

    # Dedupe only monthly_owner_notes with non-blank email
    monthly = (
        EmailDraft.objects
        .filter(product="monthly_owner_notes")
        .exclude(recipient_email="")
        .order_by("recipient_email", "period_type", "period_start", "-pk")
    )

    seen = set()
    to_delete = []
    for draft in monthly:
        key = (draft.recipient_email, draft.period_type, str(draft.period_start))
        if key in seen:
            to_delete.append(draft.pk)
        else:
            seen.add(key)

    if to_delete:
        EmailDraft.objects.filter(pk__in=to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0005_emaildraft_approved_generated_note"),
    ]

    operations = [
        # 1. Add the field
        migrations.AddField(
            model_name="emaildraft",
            name="recipient_email",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="Normalized (lowered, stripped) email address of the intended recipient.",
                max_length=254,
            ),
        ),
        # 2. Backfill + dedupe
        migrations.RunPython(backfill_and_dedupe, migrations.RunPython.noop),
        # 3. Partial unique constraint — monthly_owner_notes only, non-blank email
        migrations.AddConstraint(
            model_name="emaildraft",
            constraint=models.UniqueConstraint(
                fields=["recipient_email", "period_type", "period_start"],
                condition=models.Q(product="monthly_owner_notes") & ~models.Q(recipient_email=""),
                name="unique_monthly_note_per_email_per_period",
            ),
        ),
    ]
