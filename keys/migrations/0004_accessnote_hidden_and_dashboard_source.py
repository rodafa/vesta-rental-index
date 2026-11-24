from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("keys", "0003_pending_key_change"),
    ]

    operations = [
        migrations.AddField(
            model_name="accessnote",
            name="hidden",
            field=models.BooleanField(
                default=False,
                db_default=False,
                help_text="Hidden notes are excluded from lookups, health checks, and the dashboard.",
            ),
        ),
        migrations.AlterField(
            model_name="accessnote",
            name="source",
            field=models.CharField(
                choices=[
                    ("rentvine_unit_note", "Rentvine Unit Note"),
                    ("rentvine_property_note", "Rentvine Property Note"),
                    ("rentengine_internal_notes", "RentEngine Internal Notes"),
                    ("rentengine_key_access", "RentEngine Key Access"),
                    ("keybot", "Keybot (Slack)"),
                    ("dashboard", "Dashboard"),
                ],
                max_length=30,
            ),
        ),
    ]
