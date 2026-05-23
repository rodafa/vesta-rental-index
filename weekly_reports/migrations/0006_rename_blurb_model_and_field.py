from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('weekly_reports', '0005_add_weekly_leasing_blurb'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='WeeklyLeasingBlurb',
            new_name='OwnerEmailBlurb',
        ),
        migrations.RenameField(
            model_name='owneremailblurb',
            old_name='week_start',
            new_name='week_date',
        ),
    ]
