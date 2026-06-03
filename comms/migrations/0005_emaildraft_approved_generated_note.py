"""
Add generated_note field and approved status to EmailDraft.

generated_note stores the AI-generated plain text prose that is editable
in the dashboard textarea. body_html continues to hold the rendered email.

The approved status supports the dashboard approve→send workflow:
draft → approved → sent.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0004_emaildraft_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="emaildraft",
            name="generated_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="emaildraft",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("approved", "Approved"),
                    ("sent", "Sent"),
                ],
                db_index=True,
                default="draft",
                max_length=10,
            ),
        ),
    ]
