"""
Seed 100 properties with realistic data. 30% get full leasing data
(tenants, leases, prospects, showings, applications).
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from leasing.models import (
    Applicant,
    Application,
    Lease,
    LeasingEvent,
    Prospect,
    Showing,
    Tenant,
)
from properties.models import Floorplan, MultifamilyProperty, Owner, Portfolio, Property, Unit

# ---------------------------------------------------------------------------
# Realistic seed pools
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
    "Linda", "David", "Elizabeth", "William", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Christopher", "Karen",
    "Daniel", "Lisa", "Matthew", "Nancy", "Anthony", "Betty", "Mark",
    "Margaret", "Donald", "Sandra", "Steven", "Ashley", "Andrew", "Dorothy",
    "Paul", "Kimberly", "Joshua", "Emily", "Kenneth", "Donna",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark",
    "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King",
    "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
]

STREET_NAMES = [
    "Main St", "Oak Ave", "Maple Dr", "Cedar Ln", "Elm St", "Pine Rd",
    "Birch Ct", "Walnut St", "Cherry Ln", "Ash Blvd", "Spruce Way",
    "Willow Dr", "Hickory Rd", "Poplar Ave", "Magnolia Ln", "Cypress St",
    "Juniper Way", "Sycamore Dr", "Laurel Ct", "Chestnut Ave",
    "Dogwood Ln", "Redwood Blvd", "Aspen Rd", "Hawthorn St", "Alder Way",
    "Pecan Dr", "Sequoia Ave", "Oakwood Ct", "Linden St", "Beech Rd",
]

CITIES_AND_STATES = [
    ("Austin", "TX"), ("Dallas", "TX"), ("Houston", "TX"),
    ("San Antonio", "TX"), ("Fort Worth", "TX"), ("Plano", "TX"),
    ("Arlington", "TX"), ("Round Rock", "TX"), ("Frisco", "TX"),
    ("McKinney", "TX"), ("Georgetown", "TX"), ("Cedar Park", "TX"),
    ("Leander", "TX"), ("Pflugerville", "TX"), ("Kyle", "TX"),
    ("New Braunfels", "TX"), ("San Marcos", "TX"), ("Temple", "TX"),
    ("Killeen", "TX"), ("Waco", "TX"),
]

PROPERTY_TYPES = [
    "single_family", "apartment", "condo", "townhouse", "duplex",
    "multiplex", "loft",
]

PROPERTY_NAMES = [
    "Sunset Ridge", "Lakewood Terrace", "Oakmont Gardens", "Riverside Manor",
    "Heritage Oaks", "Willow Creek", "Stonebridge Crossing", "Meadow Glen",
    "Parkview Place", "Highland Pointe", "Canyon Vista", "Timber Ridge",
    "Autumn Hills", "Silver Lake", "Brookstone", "Eagle Landing",
    "Summit View", "Prairie Wind", "Fox Hollow", "Arbor Walk",
]

PROSPECT_SOURCES = [
    "Zillow", "Apartments.com", "Realtor.com", "Facebook Marketplace",
    "Craigslist", "Referral", "Walk-in", "Website", "Google Ads",
    "Instagram", "Yard Sign", "Trulia",
]

SHOWING_METHODS = ["accompanied", "self_guided", "remote_guided"]

EVENT_TYPES_FUNNEL = [
    "new", "contacted_awaiting_information", "showing_desired",
    "showing_scheduled", "showing_confirmed", "showing_complete",
    "application_sent_to_prospect", "application_received",
    "application_pending", "application_approved",
    "lease_out_for_signing", "lease_signed",
    "deposit_received", "move_in_scheduled", "moved_in",
]


def _rand_phone():
    return f"({random.randint(200,999)}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def _rand_email(first, last):
    domain = random.choice(["gmail.com", "yahoo.com", "outlook.com", "icloud.com"])
    return f"{first.lower()}.{last.lower()}{random.randint(1,99)}@{domain}"


def _rand_date(start_year=2022, end_year=2025):
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _rand_decimal(lo, hi, places=2):
    return Decimal(str(round(random.uniform(lo, hi), places)))


class Command(BaseCommand):
    help = "Seed 100 properties; 30% with full leasing data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing seed-able data before creating new records.",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing data...")
            for model in [
                LeasingEvent, Showing, Applicant, Application, Lease,
                Prospect, Tenant, Floorplan, MultifamilyProperty, Unit,
                Property, Owner, Portfolio,
            ]:
                count = model.objects.count()
                model.objects.all().delete()
                self.stdout.write(f"  Deleted {count} {model.__name__} records")

        self._seed()

    def _seed(self):
        rng = random.Random(42)
        random.seed(42)

        # --- Portfolios (5) ---
        portfolio_names = [
            "Vesta Core Fund", "Vesta Growth I", "Texas Residential LP",
            "Lone Star Holdings", "Metro Realty Trust",
        ]
        portfolios = []
        for i, name in enumerate(portfolio_names, start=1):
            p, _ = Portfolio.objects.get_or_create(
                rentvine_id=9000 + i,
                defaults={
                    "name": name,
                    "is_active": True,
                    "reserve_amount": _rand_decimal(5000, 50000),
                    "fiscal_year_end_month": 12,
                },
            )
            portfolios.append(p)
        self.stdout.write(f"Portfolios: {len(portfolios)}")

        # --- Owners (10) ---
        owners = []
        for i in range(10):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            o, _ = Owner.objects.get_or_create(
                rentvine_contact_id=8000 + i,
                defaults={
                    "name": f"{first} {last}",
                    "first_name": first,
                    "last_name": last,
                    "email": _rand_email(first, last),
                    "phone": _rand_phone(),
                    "is_active": True,
                },
            )
            o.portfolios.add(random.choice(portfolios))
            owners.append(o)
        self.stdout.write(f"Owners: {len(owners)}")

        # --- 100 Properties + Units ---
        properties = []
        units = []
        for i in range(100):
            city, state = random.choice(CITIES_AND_STATES)
            street_num = str(random.randint(100, 9999))
            street = random.choice(STREET_NAMES)
            addr = f"{street_num} {street}"
            prop_type = random.choice(PROPERTY_TYPES)
            is_multi = prop_type in ("apartment", "duplex", "multiplex")

            prop, _ = Property.objects.get_or_create(
                rentvine_id=1000 + i,
                defaults={
                    "rentengine_id": 2000 + i,
                    "portfolio": random.choice(portfolios),
                    "name": f"{random.choice(PROPERTY_NAMES)} #{i+1}" if is_multi else "",
                    "property_type": prop_type,
                    "is_multi_unit": is_multi,
                    "street_number": street_num,
                    "street_name": street,
                    "address_line_1": addr,
                    "city": city,
                    "state": state,
                    "postal_code": f"{random.randint(70000,79999)}",
                    "country": "US",
                    "latitude": _rand_decimal(29.5, 33.5, 7),
                    "longitude": _rand_decimal(-100.0, -95.0, 7),
                    "year_built": random.randint(1965, 2024),
                    "is_active": True,
                    "maintenance_limit_amount": _rand_decimal(200, 1000),
                    "reserve_amount": _rand_decimal(500, 5000),
                    "date_contract_begins": _rand_date(2020, 2023),
                },
            )
            properties.append(prop)

            # Units: multi-unit gets 2-8 units, single gets 1
            unit_count = random.randint(2, 8) if is_multi else 1
            for u in range(unit_count):
                beds = random.choice([1, 1, 2, 2, 2, 3, 3, 4])
                unit, _ = Unit.objects.get_or_create(
                    rentvine_id=3000 + i * 10 + u,
                    defaults={
                        "rentengine_id": 4000 + i * 10 + u,
                        "property": prop,
                        "name": f"Unit {u+1}" if is_multi else "",
                        "address_line_1": f"{addr}{f' #{u+1}' if is_multi else ''}",
                        "city": city,
                        "state": state,
                        "postal_code": prop.postal_code,
                        "bedrooms": beds,
                        "full_bathrooms": max(1, beds - random.choice([0, 0, 1])),
                        "half_bathrooms": random.choice([0, 0, 0, 1]),
                        "square_feet": beds * random.randint(350, 600) + random.randint(100, 300),
                        "target_rental_rate": _rand_decimal(
                            900 + beds * 200, 1200 + beds * 400
                        ),
                        "deposit": _rand_decimal(500, 2500),
                        "is_active": True,
                    },
                )
                units.append(unit)
        self.stdout.write(f"Properties: {len(properties)}  |  Units: {len(units)}")

        # --- Leasing data for 30% of properties ---
        leased_props = random.sample(properties, 30)
        tenant_counter = 0
        lease_counter = 0
        prospect_counter = 0
        event_counter = 0
        showing_counter = 0
        app_counter = 0

        for prop in leased_props:
            prop_units = list(prop.units.all())
            if not prop_units:
                continue

            for unit in prop_units[:random.randint(1, min(3, len(prop_units)))]:
                # --- Tenant ---
                first = random.choice(FIRST_NAMES)
                last = random.choice(LAST_NAMES)
                tenant, _ = Tenant.objects.get_or_create(
                    rentvine_contact_id=5000 + tenant_counter,
                    defaults={
                        "name": f"{first} {last}",
                        "first_name": first,
                        "last_name": last,
                        "email": _rand_email(first, last),
                        "phone": _rand_phone(),
                        "is_active": True,
                    },
                )
                tenant_counter += 1

                # --- Lease ---
                start = _rand_date(2023, 2025)
                end = start + timedelta(days=random.choice([180, 365, 365, 365, 730]))
                status = random.choices([1, 2, 3], weights=[5, 70, 25])[0]
                lease, created = Lease.objects.get_or_create(
                    rentvine_id=6000 + lease_counter,
                    defaults={
                        "unit": unit,
                        "property": prop,
                        "primary_lease_status": status,
                        "lease_status_id": status,
                        "move_in_date": start - timedelta(days=random.randint(0, 7)),
                        "start_date": start,
                        "end_date": end,
                        "closed_date": end if status == 3 else None,
                        "move_out_status": 3 if status == 3 else 1,
                        "is_renewal": random.random() < 0.25,
                    },
                )
                if created:
                    lease.tenants.add(tenant)
                    # Occasionally add a co-tenant
                    if random.random() < 0.3:
                        co_first = random.choice(FIRST_NAMES)
                        co_last = last  # same household
                        co_tenant, _ = Tenant.objects.get_or_create(
                            rentvine_contact_id=5000 + tenant_counter,
                            defaults={
                                "name": f"{co_first} {co_last}",
                                "first_name": co_first,
                                "last_name": co_last,
                                "email": _rand_email(co_first, co_last),
                                "phone": _rand_phone(),
                            },
                        )
                        tenant_counter += 1
                        lease.tenants.add(co_tenant)
                lease_counter += 1

                # --- Prospect ---
                p_first = random.choice(FIRST_NAMES)
                p_last = random.choice(LAST_NAMES)
                prospect, _ = Prospect.objects.get_or_create(
                    rentengine_id=7000 + prospect_counter,
                    defaults={
                        "unit_of_interest": unit,
                        "name": f"{p_first} {p_last}",
                        "email": _rand_email(p_first, p_last),
                        "phone": _rand_phone(),
                        "source": random.choice(PROSPECT_SOURCES),
                        "status": random.choice(["Active", "Closed", "Converted"]),
                        "source_created_at": timezone.now() - timedelta(days=random.randint(1, 180)),
                    },
                )
                prospect_counter += 1

                # --- Leasing events (walk through funnel) ---
                depth = random.randint(3, len(EVENT_TYPES_FUNNEL))
                event_date = _rand_date(2024, 2025)
                for step, etype in enumerate(EVENT_TYPES_FUNNEL[:depth]):
                    event_dt = timezone.now() - timedelta(
                        days=(depth - step) * random.randint(1, 5)
                    )
                    LeasingEvent.objects.get_or_create(
                        rentengine_id=10000 + event_counter,
                        defaults={
                            "prospect": prospect,
                            "unit": unit,
                            "event_type": etype,
                            "event_timestamp": event_dt,
                            "event_date": event_dt.date(),
                        },
                    )
                    event_counter += 1

                # --- Showing ---
                showing_dt = timezone.now() - timedelta(days=random.randint(5, 90))
                showing_status = random.choice(
                    ["completed", "completed", "completed", "missed", "canceled"]
                )
                Showing.objects.get_or_create(
                    rentengine_id=11000 + showing_counter,
                    defaults={
                        "prospect": prospect,
                        "unit": unit,
                        "showing_method": random.choice(SHOWING_METHODS),
                        "status": showing_status,
                        "scheduled_at": showing_dt,
                        "completed_at": showing_dt + timedelta(minutes=30)
                        if showing_status == "completed"
                        else None,
                    },
                )
                showing_counter += 1

                # --- Application + Applicant ---
                app_status = random.choices(
                    [2, 3, 4, 6, 7, 8], weights=[10, 15, 10, 40, 15, 10]
                )[0]
                app_obj, app_created = Application.objects.get_or_create(
                    rentvine_id=12000 + app_counter,
                    defaults={
                        "unit": unit,
                        "primary_status": app_status,
                        "application_status_id": app_status,
                        "number": f"APP-{12000 + app_counter}",
                        "address": f"{random.randint(100,9999)} {random.choice(STREET_NAMES)}",
                        "city": prop.city,
                        "state": prop.state,
                        "postal_code": prop.postal_code,
                        "source_created_at": timezone.now()
                        - timedelta(days=random.randint(10, 120)),
                    },
                )
                if app_created:
                    Applicant.objects.get_or_create(
                        rentvine_id=13000 + app_counter,
                        defaults={
                            "application": app_obj,
                            "name": prospect.name,
                            "email": prospect.email,
                            "phone": prospect.phone,
                        },
                    )
                app_counter += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone! Created:\n"
                f"  Portfolios:     {len(portfolios)}\n"
                f"  Owners:         {len(owners)}\n"
                f"  Properties:     {len(properties)}\n"
                f"  Units:          {len(units)}\n"
                f"  Tenants:        {tenant_counter}\n"
                f"  Leases:         {lease_counter}\n"
                f"  Prospects:      {prospect_counter}\n"
                f"  LeasingEvents:  {event_counter}\n"
                f"  Showings:       {showing_counter}\n"
                f"  Applications:   {app_counter}"
            )
        )
