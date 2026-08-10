import django.core.serializers.json
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("comms", "0011_emaildraft_sent_fragments"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliomaintenancenote",
            name="work_order_snapshot",
            field=models.JSONField(
                blank=True,
                default=dict,
                encoder=django.core.serializers.json.DjangoJSONEncoder,
                help_text=(
                    "Frozen structured work-order payload (buckets, per-WO facts + AI "
                    "summaries, intro) captured at generation. Source for rebuilding "
                    "notes_html on edit. Write-only for now — nothing reads it yet."
                ),
            ),
        ),
    ]
