# Generated migration for OwnerReportNote delivery tracking fields

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0004_add_staff_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='ownerreportnote',
            name='delivered_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ownerreportnote',
            name='bounce_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='ownerreportnote',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('reviewed', 'Reviewed'),
                    ('sent', 'Sent'),
                    ('delivered', 'Delivered'),
                    ('bounced', 'Bounced'),
                ],
                db_index=True,
                default='draft',
                max_length=10,
            ),
        ),
    ]
