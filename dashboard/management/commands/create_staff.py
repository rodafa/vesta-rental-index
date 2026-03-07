import secrets
import string

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from dashboard.models import StaffProfile

STAFF_USERS = [
    {"username": "rodrigo", "email": "rodrigo@vestapm.com", "first_name": "Rodrigo"},
    {"username": "luci", "email": "luci@vestapm.com", "first_name": "Luci"},
    {"username": "lisa", "email": "lisa@vestapm.com", "first_name": "Lisa"},
    {"username": "bill", "email": "bill@vestapm.com", "first_name": "Bill"},
]


def generate_temp_password(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Command(BaseCommand):
    help = "Create all Vesta staff accounts with temporary passwords"

    def handle(self, *args, **options):
        created = []
        for info in STAFF_USERS:
            if User.objects.filter(username=info["username"]).exists():
                self.stdout.write(
                    self.style.WARNING(
                        f"  User '{info['username']}' already exists — skipping"
                    )
                )
                continue

            temp_pw = generate_temp_password()
            user = User.objects.create_user(
                username=info["username"],
                email=info["email"],
                password=temp_pw,
                first_name=info["first_name"],
                is_staff=True,
            )
            StaffProfile.objects.get_or_create(
                user=user, defaults={"must_change_password": True}
            )
            created.append((info["username"], info["email"], temp_pw))

        if created:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("Created staff accounts:"))
            self.stdout.write("-" * 50)
            for username, email, pw in created:
                self.stdout.write(f"  {username} ({email})")
                self.stdout.write(f"    Temp password: {pw}")
            self.stdout.write("-" * 50)
            self.stdout.write(
                self.style.WARNING(
                    "Users must change password on first login."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("All staff accounts already exist.")
            )
