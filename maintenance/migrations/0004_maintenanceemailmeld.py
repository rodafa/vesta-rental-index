import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0003_extend_meld_add_expenditure_emailsend"),
        ("properties", "0005_add_property_meld_ids"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceEmailMeld",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meld_reference_id", models.CharField(blank=True, max_length=20)),
                ("summary_text", models.TextField(blank=True)),
                ("cost", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("status_at_send", models.CharField(blank=True, max_length=100)),
                ("section", models.CharField(max_length=10)),
                ("portfolio_name", models.CharField(blank=True, max_length=255)),
                ("unit_label", models.CharField(blank=True, max_length=500)),
                ("property_address", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "email_send",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="meld_snapshots",
                        to="maintenance.maintenanceemailsend",
                    ),
                ),
                (
                    "meld",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="email_snapshots",
                        to="maintenance.meld",
                    ),
                ),
                (
                    "portfolio",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="maintenance_email_melds",
                        to="properties.portfolio",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["portfolio", "created_at"], name="maintenance_portfol_8f3c1a_idx"),
                    models.Index(fields=["email_send"], name="maintenance_email_s_4b2e7d_idx"),
                ],
            },
        ),
    ]
