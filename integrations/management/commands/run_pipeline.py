import logging
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)

# Pipeline steps in dependency order.
# Each entry: (label, management_command, args, kwargs)
PIPELINE = [
    ("RentVine sync", "sync_rentvine_all", [], {}),
    ("RentEngine sync", "sync_rentengine_all", [], {}),
    ("BoomPay sync", "sync_boompay_all", [], {}),
    ("Market aggregation", "aggregate_market_data", [], {"all": True}),
    ("Owner-portfolio links", "link_owner_portfolios", [], {}),
]


class Command(BaseCommand):
    help = (
        "Run the full Vesta data pipeline: "
        "RentVine -> RentEngine -> BoomPay -> Market Aggregation -> Owner Links. "
        "Designed to be called by Railway cron or manually."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--include-reports",
            action="store_true",
            help="Also generate weekly owner reports (run on Mondays).",
        )
        parser.add_argument(
            "--skip",
            nargs="+",
            choices=["rentvine", "rentengine", "boompay", "aggregate", "links"],
            default=[],
            help="Skip specific pipeline steps.",
        )

    def handle(self, *args, **options):
        skip = set(options["skip"])
        include_reports = options["include_reports"]

        skip_map = {
            "rentvine": "RentVine sync",
            "rentengine": "RentEngine sync",
            "boompay": "BoomPay sync",
            "aggregate": "Market aggregation",
            "links": "Owner-portfolio links",
        }

        overall_start = time.time()
        results = []

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write("Vesta Data Pipeline")
        self.stdout.write(f"{'='*60}")

        for label, cmd, cmd_args, cmd_kwargs in PIPELINE:
            # Check if this step should be skipped
            if any(skip_map.get(s) == label for s in skip):
                self.stdout.write(f"\n  SKIP  {label}")
                results.append((label, "skipped", 0))
                continue

            self.stdout.write(f"\n  >>>   {label}")
            step_start = time.time()

            try:
                call_command(cmd, *cmd_args, stdout=self.stdout, **cmd_kwargs)
                elapsed = time.time() - step_start
                self.stdout.write(self.style.SUCCESS(
                    f"  OK    {label} ({elapsed:.1f}s)"
                ))
                results.append((label, "ok", elapsed))
            except Exception as exc:
                elapsed = time.time() - step_start
                self.stdout.write(self.style.ERROR(
                    f"  FAIL  {label} ({elapsed:.1f}s) - {exc}"
                ))
                logger.exception("Pipeline step failed: %s", label)
                results.append((label, "failed", elapsed))

        # Optional: generate weekly owner reports
        if include_reports:
            self.stdout.write(f"\n  >>>   Weekly owner reports")
            step_start = time.time()
            try:
                call_command("generate_weekly_reports", stdout=self.stdout)
                elapsed = time.time() - step_start
                self.stdout.write(self.style.SUCCESS(
                    f"  OK    Weekly owner reports ({elapsed:.1f}s)"
                ))
                results.append(("Weekly owner reports", "ok", elapsed))
            except Exception as exc:
                elapsed = time.time() - step_start
                self.stdout.write(self.style.ERROR(
                    f"  FAIL  Weekly owner reports ({elapsed:.1f}s) - {exc}"
                ))
                results.append(("Weekly owner reports", "failed", elapsed))

        # Summary
        total_elapsed = time.time() - overall_start
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Pipeline complete in {total_elapsed:.1f}s")
        self.stdout.write(f"{'='*60}")

        for label, status, elapsed in results:
            if status == "ok":
                self.stdout.write(self.style.SUCCESS(f"  {label}: OK ({elapsed:.1f}s)"))
            elif status == "failed":
                self.stdout.write(self.style.ERROR(f"  {label}: FAILED ({elapsed:.1f}s)"))
            else:
                self.stdout.write(f"  {label}: SKIPPED")

        failed = [r for r in results if r[1] == "failed"]
        if failed:
            self.stdout.write(self.style.WARNING(
                f"\n{len(failed)} step(s) failed. Check logs above."
            ))
