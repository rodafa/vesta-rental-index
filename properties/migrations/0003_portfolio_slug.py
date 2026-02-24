# Add slug field to Portfolio, populate existing rows, then enforce unique.

from django.db import migrations, models
from django.utils.text import slugify


def populate_slugs(apps, schema_editor):
    Portfolio = apps.get_model("properties", "Portfolio")
    used = set()
    for portfolio in Portfolio.objects.all().order_by("id"):
        base = slugify(portfolio.name) or "portfolio"
        slug = base
        n = 1
        while slug in used or Portfolio.objects.filter(slug=slug).exclude(pk=portfolio.pk).exists():
            slug = f"{base}-{n}"
            n += 1
        portfolio.slug = slug
        portfolio.save(update_fields=["slug"])
        used.add(slug)


class Migration(migrations.Migration):

    dependencies = [
        ("properties", "0002_property_service_type"),
    ]

    operations = [
        # Step 1: Add slug as a plain VARCHAR, no indexes
        migrations.AddField(
            model_name="portfolio",
            name="slug",
            field=models.CharField(max_length=255, default="", blank=True),
        ),
        # Step 2: Populate slugs for all existing portfolios
        migrations.RunPython(populate_slugs, migrations.RunPython.noop),
        # Step 3: Convert to SlugField with unique constraint
        migrations.AlterField(
            model_name="portfolio",
            name="slug",
            field=models.SlugField(max_length=255, unique=True, blank=True),
        ),
    ]
