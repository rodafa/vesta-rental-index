from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="ownerreportlog",
            old_name="property_address",
            new_name="portfolio_name",
        ),
    ]
