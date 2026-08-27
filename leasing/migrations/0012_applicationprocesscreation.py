from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("leasing", "0011_unitleasingsnapshot_segment_benchmark"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApplicationProcessCreation",
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
                    "rentengine_group_id",
                    models.CharField(
                        db_index=True, max_length=64, unique=True,
                    ),
                ),
                (
                    "rentengine_event_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=128,
                    ),
                ),
                (
                    "leadsimple_process_id",
                    models.CharField(
                        blank=True, default="", max_length=64,
                    ),
                ),
                (
                    "process_name",
                    models.CharField(
                        blank=True, default="", max_length=255,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True, db_index=True,
                    ),
                ),
            ],
        ),
    ]
