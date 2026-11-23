from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("comms", "0014_add_validation_flags_to_portfoliomonthlynote"),
    ]

    operations = [
        migrations.AddField(
            model_name="portfoliomaintenancenote",
            name="approved_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When status was last set to 'approved'.",
            ),
        ),
        migrations.AddField(
            model_name="portfoliomaintenancenote",
            name="approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
