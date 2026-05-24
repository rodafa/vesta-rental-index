# Generated manually for PropertyMeld maintenance email feature

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0002_meld"),
        ("properties", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- Rename completed_date → marked_complete and change to DateTimeField ---
        migrations.RenameField(
            model_name="meld",
            old_name="completed_date",
            new_name="marked_complete",
        ),
        migrations.AlterField(
            model_name="meld",
            name="marked_complete",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        # --- New fields on Meld ---
        migrations.AddField(
            model_name="meld",
            name="reference_id",
            field=models.CharField(blank=True, db_index=True, max_length=20),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="description",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="work_type",
            field=models.CharField(blank=True, max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="work_location",
            field=models.CharField(blank=True, max_length=255),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="origin",
            field=models.CharField(blank=True, max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="meld",
            name="started",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="meld",
            name="due_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="meld",
            name="completion_date",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="meld",
            name="maintenance_notes",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="completion_notes",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="reason_cannot_complete",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="resolution_type",
            field=models.CharField(blank=True, max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="parent_meld_id",
            field=models.CharField(blank=True, max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="recurring_meld_id",
            field=models.CharField(blank=True, max_length=50),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="merged_meld_data",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="meld",
            name="tenant_rating",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="meld",
            name="unit_fk",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="melds",
                to="properties.unit",
            ),
        ),
        migrations.AddField(
            model_name="meld",
            name="ai_summary",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="staff_summary",
            field=models.TextField(blank=True, default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="meld",
            name="summary_status",
            field=models.CharField(
                choices=[("auto", "Auto"), ("edited", "Edited"), ("needs_manual", "Needs Manual")],
                default="auto",
                max_length=20,
            ),
        ),
        # --- Update index (completed_date → marked_complete) ---
        migrations.RemoveIndex(
            model_name="meld",
            name="maintenance_complet_a4dfe1_idx",
        ),
        migrations.AddIndex(
            model_name="meld",
            index=models.Index(
                fields=["marked_complete", "has_invoice"],
                name="maintenance_marked__a4dfe1_idx",
            ),
        ),
        # --- New model: Expenditure ---
        migrations.CreateModel(
            name="Expenditure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("property_meld_id", models.IntegerField(db_index=True, unique=True)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("status", models.CharField(max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("line_items", models.JSONField(blank=True, default=list)),
                ("source_created_at", models.DateTimeField(blank=True, null=True)),
                ("source_updated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "meld",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="expenditures",
                        to="maintenance.meld",
                    ),
                ),
            ],
        ),
        # --- New model: MaintenanceEmailSend ---
        migrations.CreateModel(
            name="MaintenanceEmailSend",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_date", models.DateField()),
                ("status", models.CharField(max_length=20)),
                ("sendgrid_message_id", models.CharField(blank=True, max_length=255)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("error_detail", models.TextField(blank=True)),
                ("melds_included", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="maintenance_email_sends",
                        to="properties.owner",
                    ),
                ),
                (
                    "sent_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "unique_together": {("owner", "week_date")},
            },
        ),
    ]
