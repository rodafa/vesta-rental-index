from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0012_portfoliomaintenancenote_work_order_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliomaintenancenote",
            name="is_edited",
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text=(
                    "True once a human has edited this note's snapshot. Protects it "
                    "from being overwritten on regeneration. Set False on fresh generation."
                ),
            ),
        ),
    ]
