from django.db import migrations, models


def convert_success_to_pending(apps, schema_editor):
    OwnerReportLog = apps.get_model("reports", "OwnerReportLog")
    OwnerReportLog.objects.filter(status="success").update(status="pending")


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0002_rename_property_address_to_portfolio_name"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ownerreportlog",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("sent", "Sent"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="statement_period",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="beginning_balance",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="total_income",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="total_expenses",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="total_adjustments",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="ending_balance",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="total_distribution",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ownerreportlog",
            name="sendgrid_message_id",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(
            convert_success_to_pending,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
