# Generated manually for ApplicantScorecard model

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('screening', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ApplicantScorecard',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                # Income & Employment
                ('income_ratio', models.CharField(blank=True, choices=[('3x_plus', '3x+ Rent (30 pts)'), ('2.5x_to_3x', '2.5x-3x Rent (20 pts)'), ('2x_to_2.5x', '2x-2.5x Rent (10 pts)'), ('below_2x', 'Below 2x Rent (0 pts)')], max_length=20)),
                ('income_ratio_numeric', models.DecimalField(blank=True, decimal_places=2, help_text='Actual income-to-rent ratio (e.g. 3.20)', max_digits=5, null=True)),
                ('income_employment_verified', models.BooleanField(default=False)),
                ('income_savings_verified', models.BooleanField(default=False)),
                # Pets & ESA
                ('pet_status', models.CharField(blank=True, choices=[('no_pets', 'No Pets (10 pts)'), ('low_risk', 'Low Risk Pet (7 pts)'), ('medium_risk', 'Medium Risk Pet (4 pts)'), ('high_risk', 'High Risk Pet (0 pts)')], max_length=20)),
                # Credit & Financial
                ('credit_tier', models.CharField(blank=True, choices=[('excellent', '750+ (15 pts)'), ('good', '700-749 (12 pts)'), ('fair', '650-699 (8 pts)'), ('poor', '600-649 (4 pts)'), ('very_poor', 'Below 600 (0 pts)')], max_length=20)),
                ('credit_score_raw', models.IntegerField(blank=True, null=True)),
                ('bankruptcy_status', models.CharField(blank=True, choices=[('none', 'No Bankruptcy (5 pts)'), ('discharged_3plus', 'Discharged 3+ yrs (3 pts)'), ('discharged_recent', 'Discharged < 3 yrs (1 pt)'), ('active', 'Active Bankruptcy (0 pts)')], max_length=20)),
                ('credit_active_chargeoffs', models.BooleanField(default=False)),
                ('dti_tier', models.CharField(blank=True, choices=[('low', 'DTI < 30% (5 pts)'), ('moderate', 'DTI 30-45% (3 pts)'), ('high', 'DTI > 45% (0 pts)')], max_length=20)),
                ('dti_numeric', models.DecimalField(blank=True, decimal_places=2, help_text='Debt-to-income ratio (e.g. 35.50)', max_digits=5, null=True)),
                # Rental History
                ('rental_positive_ref', models.BooleanField(default=False)),
                ('rental_would_rent_again', models.BooleanField(default=False)),
                ('rental_no_complaints', models.BooleanField(default=False)),
                ('eviction_history', models.CharField(blank=True, choices=[('none', 'No Evictions (10 pts)'), ('old', 'Eviction 5+ yrs ago (5 pts)'), ('recent', 'Eviction < 5 yrs (0 pts)')], max_length=20)),
                ('rental_owes_landlord', models.BooleanField(default=False)),
                # Legal History
                ('legal_no_felonies_5yr', models.BooleanField(default=False)),
                ('legal_nonviolent_felony_over_5yr', models.BooleanField(default=False)),
                ('legal_drug_misdemeanor_3yr', models.BooleanField(default=False)),
                ('legal_other_misdemeanor_3yr', models.IntegerField(default=0, help_text='Count of other misdemeanors in past 3 years')),
                ('legal_settled_small_claims_3yr', models.BooleanField(default=False)),
                ('legal_open_small_claims', models.BooleanField(default=False)),
                ('legal_settled_landlord_tenant', models.BooleanField(default=False)),
                ('legal_unpaid_landlord_judgment', models.BooleanField(default=False)),
                # Application
                ('app_completed', models.BooleanField(default=False)),
                ('app_docs_verified', models.BooleanField(default=False)),
                ('app_good_communication', models.BooleanField(default=False)),
                ('app_on_time_appointment', models.BooleanField(default=False)),
                # Co-Signer
                ('cosigner_strength', models.CharField(blank=True, choices=[('none', 'No Co-Signer (0 pts)'), ('weak', 'Weak Co-Signer (5 pts)'), ('moderate', 'Moderate Co-Signer (10 pts)'), ('strong', 'Strong Co-Signer (15 pts)')], max_length=20)),
                # Auto-deny flags
                ('auto_deny_false_info', models.BooleanField(default=False)),
                ('auto_deny_recent_eviction', models.BooleanField(default=False)),
                ('auto_deny_violent_felony', models.BooleanField(default=False)),
                ('auto_deny_pet_not_disclosed', models.BooleanField(default=False)),
                ('auto_deny_no_cosigner', models.BooleanField(default=False)),
                # Computed
                ('total_score', models.IntegerField(default=0)),
                ('recommendation', models.CharField(blank=True, choices=[('platinum', 'Platinum (100-110+)'), ('strong', 'Strong (80-99)'), ('borderline', 'Borderline (60-79)'), ('high_risk', 'High Risk (40-59)'), ('reject', 'Reject (Below 40)'), ('auto_deny', 'Auto-Deny')], max_length=20)),
                # Audit
                ('reviewed_by', models.CharField(blank=True, max_length=100)),
                ('notes', models.TextField(blank=True)),
                ('auto_populated', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                # FK
                ('screening_application', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='scorecard', to='screening.screeningapplication')),
            ],
        ),
    ]
