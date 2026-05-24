# Generated manually for PropertyMeld cross-reference

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0004_add_zillow_url_to_unit"),
    ]

    operations = [
        migrations.AddField(
            model_name="property",
            name="property_meld_id",
            field=models.IntegerField(
                blank=True, db_index=True, null=True, unique=True
            ),
        ),
        migrations.AddField(
            model_name="unit",
            name="property_meld_id",
            field=models.IntegerField(
                blank=True, db_index=True, null=True, unique=True
            ),
        ),
    ]
