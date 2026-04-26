from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OwnerReportLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("owner_id", models.CharField(max_length=100)),
                ("owner_name", models.CharField(max_length=255)),
                ("report_month", models.DateField()),
                ("property_address", models.CharField(max_length=255)),
                ("status", models.CharField(max_length=20)),
                ("error_message", models.TextField(blank=True)),
                ("report_data", models.JSONField(blank=True, null=True)),
                ("generated_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="ownerreportlog",
            index=models.Index(
                fields=["owner_id", "report_month"],
                name="reports_own_owner_i_idx",
            ),
        ),
    ]
