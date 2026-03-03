from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from dashboard.models import StaffProfile


class Command(BaseCommand):
    help = "Create a staff user with forced password change"

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        username = options["username"]
        email = options["email"]
        password = options["password"]

        if User.objects.filter(username=username).exists():
            self.stdout.write(self.style.WARNING(f"User '{username}' already exists — skipping"))
            return

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            is_staff=True,
        )
        StaffProfile.objects.create(user=user, must_change_password=True)
        self.stdout.write(self.style.SUCCESS(f"Created staff user '{username}' — must change password on first login"))
