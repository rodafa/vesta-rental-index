"""
Add PortfolioStatement model for syncing owner statements from RentVine.
"""

import decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PortfolioStatement",
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
                (
                    "rentvine_statement_id",
                    models.IntegerField(db_index=True, unique=True),
                ),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                (
                    "beginning_balance",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "total_income",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "total_expenses",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "total_adjustments",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "ending_balance",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "total_distribution",
                    models.DecimalField(
                        decimal_places=2,
                        default=decimal.Decimal("0"),
                        max_digits=12,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        help_text="Raw statementStatusID from RentVine (2 = posted).",
                        max_length=20,
                    ),
                ),
                (
                    "raw_data",
                    models.JSONField(blank=True, default=dict),
                ),
                ("synced_at", models.DateTimeField(auto_now=True)),
                (
                    "portfolio",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="statements",
                        to="core.portfolio",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["portfolio", "-period_end"],
                        name="accounting__portfol_0e9334_idx",
                    ),
                ],
            },
        ),
    ]
