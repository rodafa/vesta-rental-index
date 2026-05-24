"""
create_listing_email.py
=======================
Build and push a MailerLite campaign for any Vesta rental listing.

Usage (inside Docker):
    docker compose exec web python create_listing_email.py "110 Heywood Rd"
    docker compose exec web python create_listing_email.py "78 Old Oak Hill"
    docker compose exec web python create_listing_email.py "110 Heywood" --dry-run
    docker compose exec web python create_listing_email.py "110 Heywood" --segment 129568169154054126

What it does:
  1. Searches RentVine for the property by address
  2. Gets unit details (rent, sqft, beds/baths, available date)
  3. Finds the matching RentEngine unit for the listing URL and CDN photos
  4. Downloads photos from RentVine CDN, uploads to MailerLite (permanent URLs)
  5. Loads base_template.html, swaps in all listing-specific content
  6. Creates and pushes the MailerLite campaign to the Prospective Tenants segment

Requires:
  - /app/base_template.html (extracted from existing "New Rental Available" campaign)
  - MAILERLITE_API_KEY, RENTVINE_API_KEY, RENTVINE_API_SECRET, RENTENGINE_API_TOKEN in env
"""
import django, os, sys, json, argparse
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vesta_rental_index.settings")
django.setup()

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ML_KEY = os.environ["MAILERLITE_API_KEY"]
ML_HEADERS = {"Authorization": "Bearer " + ML_KEY, "Content-Type": "application/json", "Accept": "application/json"}
ML_BASE = "https://connect.mailerlite.com/api"
DEFAULT_SEGMENT = "129568169154054126"  # Prospective Tenants

RV_BASE = "https://vestapm.rentvine.com/api/manager"
RV_SESSION = requests.Session()
RV_SESSION.auth = (os.environ["RENTVINE_API_KEY"], os.environ["RENTVINE_API_SECRET"])
RV_SESSION.headers.update({"Accept": "application/json"})

RE_BASE = "https://app.rentengine.io/api/public/v1"
RE_HEADERS = {"Authorization": "Bearer " + os.environ.get("RENTENGINE_API_TOKEN", ""), "Accept": "application/json"}
RE_ACCOUNT = "c26fbfed-dd3d-4c91-a198-b4d3d8259712"

RV_CDN = "https://cdn.rentvine.com/vestapm/properties"

# ---------------------------------------------------------------------------
# Step 1: Find property in RentVine
# ---------------------------------------------------------------------------
def find_rv_property(search_term):
    r = RV_SESSION.get(RV_BASE + "/properties", params={"search": search_term, "pageSize": 10})
    r.raise_for_status()
    results = r.json()
    if not results:
        raise SystemExit("No RentVine property found for: " + search_term)
    # Pick best match (first result)
    prop = results[0]["property"]
    token = results[0]["token"]
    print("RentVine property: %s (ID %s)" % (prop["name"], prop["propertyID"]))
    return prop, token

# ---------------------------------------------------------------------------
# Step 2: Get unit details from RentVine
# ---------------------------------------------------------------------------
def get_rv_unit(property_id):
    r = RV_SESSION.get(RV_BASE + "/properties/" + property_id + "/units")
    r.raise_for_status()
    units = r.json()
    if not units:
        raise SystemExit("No units found for property " + property_id)
    # Return the wrapper dict (has unit + token)
    return units[0]

# ---------------------------------------------------------------------------
# Step 3: Get images from RentVine
# ---------------------------------------------------------------------------
def get_rv_images(property_id, count=5):
    r = RV_SESSION.get(RV_BASE + "/properties/" + property_id + "/images")
    r.raise_for_status()
    images = r.json()
    return [img["image"]["filePath"] for img in images[:count]]

# ---------------------------------------------------------------------------
# Step 4: Find RentEngine unit by address
# ---------------------------------------------------------------------------
def find_re_unit(street_number, street_name):
    all_units = []
    page = 0
    while True:
        r = requests.get(RE_BASE + "/units", headers=RE_HEADERS, params={"limit": 100, "page_number": page})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        all_units.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    for u in all_units:
        addr = u.get("address", {})
        if (str(addr.get("street_number", "")).lower() == str(street_number).lower() and
                street_name.lower() in str(addr.get("street_name", "")).lower()):
            print("RentEngine unit: %s (ID %s)" % (addr.get("formatted_address"), u["id"]))
            return u
    return None

# ---------------------------------------------------------------------------
# Step 5: Upload images to MailerLite CDN
# ---------------------------------------------------------------------------
def upload_images_to_mailerlite(property_id, file_paths):
    urls = []
    for i, fp in enumerate(file_paths):
        cdn_url = "%s/%s/images/%s/large.jpg" % (RV_CDN, property_id, fp)
        print("  Downloading image %d..." % (i + 1))
        img = requests.get(cdn_url)
        if img.status_code != 200:
            print("  WARN: Could not download %s (status %d)" % (cdn_url, img.status_code))
            urls.append(None)
            continue
        r = requests.post(
            ML_BASE + "/images",
            headers={"Authorization": "Bearer " + ML_KEY, "Accept": "application/json"},
            files={"file": ("listing_photo_%d.jpg" % (i + 1), img.content, "image/jpeg")},
            data={"name": "listing_photo_%d" % (i + 1)},
        )
        if r.status_code in (200, 201):
            url = r.json()["data"]["url"]
            print("  Uploaded: " + url)
            urls.append(url)
        else:
            print("  WARN: Upload failed: " + r.text[:100])
            urls.append(None)
    return urls

# ---------------------------------------------------------------------------
# Step 6: Build HTML from base template
# ---------------------------------------------------------------------------
def build_html(prop, unit_data, re_unit, ml_image_urls, base_template_path="/app/base_template.html"):
    with open(base_template_path, "r") as f:
        html = f.read()

    unit = unit_data.get("unit", unit_data)
    addr = unit.get("address", prop.get("address", "")) + ((" " + unit.get("address2", prop.get("address2", ""))) if unit.get("address2") or prop.get("address2") else "")
    city = unit.get("city", prop.get("city", ""))
    state = unit.get("stateID", prop.get("stateID", ""))
    zip_code = unit.get("postalCode", prop.get("postalCode", ""))
    full_addr = "%s, %s %s %s" % (addr, city, state, zip_code)

    beds = unit.get("beds", "?")
    baths = unit.get("fullBaths", "?")
    sqft = unit.get("size", "?")
    rent = unit.get("rent", "?")
    try:
        rent_fmt = "${:,.0f}/month".format(float(rent))
    except (TypeError, ValueError):
        rent_fmt = str(rent) + "/month"
    try:
        sqft_fmt = "{:,.0f}".format(float(sqft))
    except (TypeError, ValueError):
        sqft_fmt = str(sqft)

    re_listing_url = "https://www.rentengine.io/listings/%s?accounts=%s" % (re_unit["id"], RE_ACCOUNT) if re_unit else "https://vestapm.com/rentals/"
    apply_url = re_unit.get("custom_application_url", "https://vestapm.com/apply/") if re_unit else "https://vestapm.com/apply/"

    hero_url = ml_image_urls[0] if ml_image_urls and ml_image_urls[0] else ""
    ph_urls = ml_image_urls[1:5] if len(ml_image_urls) > 1 else []

    # Marketing description from RentEngine if available
    description = ""
    if re_unit and re_unit.get("marketing_description"):
        desc = re_unit["marketing_description"]
        # Take first two paragraphs
        parts = desc.split("\n\n")
        description = "<br><br>".join(parts[:2]).replace("\n", "<br>")
    if not description:
        description = (
            "Professionally managed by Vesta Property Management. "
            "This %s bed / %s bath home at %s is available now at %s/month. "
            "Apply online at vestapm.com." % (beds, baths, full_addr, rent_fmt)
        )

    # Candler → Heywood swaps using the base template values
    LISTING_URL_OLD = 'href="https://www.rentengine.io/listings/39485?accounts=c26fbfed-dd3d-4c91-a198-b4d3d8259712" target="_blank"'
    LISTING_URL_NEW = 'href="%s" target="_blank"' % re_listing_url
    APPLY_URL_OLD   = 'href="https://screen.boompay.app/a/Oti6BjmKy1CLXrmcO9UF" target="_blank"'
    APPLY_URL_NEW   = 'href="%s" target="_blank"' % apply_url

    HERO_OLD = "https://storage.mlcdn.com/account_image/921975/P3Mnt0h0OESp0r0uSyuHJv1UJHPOeuaJgseXN2Kn.jpg"
    PH_OLDS = [
        "https://storage.mlcdn.com/account_image/921975/EOc4QQ4iz1n6j9raYJyXRgBAVrZYh5wIgpVJqOqo.jpg",
        "https://storage.mlcdn.com/account_image/921975/sBsNgsySLULqm5ekmcmYN3Wr76tFfDSsna9FjepA.jpg",
        "https://storage.mlcdn.com/account_image/921975/nxEJGkjzbXEM0gdOTwjIma8B33q0OxN4Z6Jc0md9.jpg",
        "https://storage.mlcdn.com/account_image/921975/AxUHoWWS7LwVKeI48sue8zkNHUwRcGt0FclOSian.jpg",
    ]

    bed_baths = "%sBR / %sBA" % (beds, baths)
    title = "%s — %s, NC" % (bed_baths, city)

    swaps = [
        ("2BR / 1BA Candler Home with Fenced Yard!", title),
        (HERO_OLD, hero_url),
        ("2BR/1BA Home with Fenced Yard + Prime Candler Location", title),
        ("Older house, recently renovated close to everything", "Professionally Managed &bull; Minutes from Downtown Asheville"),
        (LISTING_URL_OLD, LISTING_URL_NEW),
    ]

    # Photo swaps
    for old_url, new_url in zip(PH_OLDS, ph_urls):
        if new_url:
            swaps.append((old_url, new_url))

    swaps += [
        ("<strong>🛁 Bathrooms:</strong> 1", "<strong>🛁 Bathrooms:</strong> %s" % baths),
        ("<strong>📐 Sq Ft:</strong> 1,106", "<strong>📐 Sq Ft:</strong> %s" % sqft_fmt),
        ("<strong>📍 Address:</strong> 78 Old Oak Hill Rd. Candler&nbsp;", "<strong>📍 Address:</strong> %s" % full_addr),
        ("<strong>💰 Rent:</strong> $1,550/month", "<strong>💰 Rent:</strong> %s" % rent_fmt),
        ("<strong>📅 Available:</strong>&nbsp;April 5, 2025", "<strong>📅 Available:</strong> Now"),
        (
            "Located just off Starnes Cove Road, this residence offers the ideal gateway to everything West Asheville and Downtown have to offer while providing a private, peaceful retreat.<br><br>The Space Step inside to a generous 1,106 sq. ft. layout featuring a massive 21'0\" x 17'4\" living room, perfect for relaxing or hosting guests. The spacious eat-in kitchen and dining area (over 20' wide combined) provide ample room for home-cooked meals. Both bedrooms are well-proportioned, and the entire home has been updated within the last three years with modern finishes.<br><br>Outdoor Living This home truly shines outdoors! Enjoy a large private deck (over 27' long), a covered porch, and a dedicated patio area. The yard is fully fenced, offering security and privacy, complemented by a private driveway.<br>",
            description,
        ),
        (LISTING_URL_OLD, LISTING_URL_NEW),  # second occurrence (Schedule Viewing button)
        (APPLY_URL_OLD, APPLY_URL_NEW),
        ("<div>👉 SCHEDULE VIEWING</div>", "<div>&#128205; SCHEDULE A SHOWING</div>"),
        ("Got Property Management Questions?", "Interested in This Home? Let&rsquo;s Connect."),
    ]

    for old, new in swaps:
        html = html.replace(old, new)

    return html, title

# ---------------------------------------------------------------------------
# Step 7: Create MailerLite campaign
# ---------------------------------------------------------------------------
def create_campaign(title, subject, html, segment_id, dry_run=False):
    if dry_run:
        print("[DRY RUN] Would create campaign: " + title)
        print("[DRY RUN] HTML length: %d chars" % len(html))
        return None

    r = requests.post(ML_BASE + "/campaigns", headers=ML_HEADERS, json={
        "name": title,
        "type": "regular",
        "emails": [{"subject": subject, "from_name": "Vesta Property Management", "from": "support@vestapm.com"}],
    })
    if r.status_code not in (200, 201):
        raise SystemExit("Campaign create failed: " + r.text)
    campaign_id = r.json()["data"]["id"]

    r2 = requests.put(ML_BASE + "/campaigns/" + campaign_id, headers=ML_HEADERS, json={
        "name": title,
        "type": "regular",
        "emails": [{"subject": subject, "from_name": "Vesta Property Management", "from": "support@vestapm.com", "content": html}],
        "segments": [segment_id],
    })
    if r2.status_code != 200:
        raise SystemExit("Campaign update failed: " + r2.text)

    audience = r2.json().get("data", {}).get("filter_for_humans", [])
    print("Audience: " + str(audience))
    return campaign_id

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Build a MailerLite listing email from RentVine/RentEngine data")
    parser.add_argument("address", help="Street address to search (e.g. '110 Heywood Rd')")
    parser.add_argument("--segment", default=DEFAULT_SEGMENT, help="MailerLite segment ID")
    parser.add_argument("--dry-run", action="store_true", help="Build HTML but don't push to MailerLite")
    args = parser.parse_args()

    print("\n=== Step 1: RentVine property lookup ===")
    prop, _ = find_rv_property(args.address)
    property_id = prop["propertyID"]

    print("\n=== Step 2: RentVine unit details ===")
    unit_wrapper = get_rv_unit(property_id)
    unit = unit_wrapper.get("unit", unit_wrapper)
    print("Unit: %s bed / %s bath, %s sqft, $%s/mo" % (
        unit.get("beds", "?"),
        unit.get("fullBaths", "?"),
        unit.get("size", "?"),
        unit.get("rent", "?"),
    ))

    print("\n=== Step 3: RentVine images ===")
    file_paths = get_rv_images(property_id, count=5)
    print("Found %d images" % len(file_paths))

    print("\n=== Step 4: RentEngine unit ===")
    re_unit = find_re_unit(prop.get("streetNumber", ""), prop.get("streetName", ""))
    if not re_unit:
        print("WARN: No RentEngine unit found — listing URL will fall back to vestapm.com/rentals/")

    print("\n=== Step 5: Upload images to MailerLite ===")
    ml_urls = upload_images_to_mailerlite(property_id, file_paths)

    print("\n=== Step 6: Build HTML ===")
    html, title = build_html(prop, unit_wrapper, re_unit, ml_urls)
    print("HTML length: %d chars" % len(html))

    city = unit.get("city", prop.get("city", "NC"))
    beds = unit.get("beds", "?")
    baths = unit.get("fullBaths", "?")
    rent = unit.get("rent", "?")
    try:
        rent_fmt = "${:,.0f}/mo".format(float(rent))
    except (TypeError, ValueError):
        rent_fmt = str(rent) + "/mo"

    subject = "Now Available: %sBR/%sBA in %s — %s | Vesta" % (beds, baths, city, rent_fmt)
    full_title = "%s — %s, NC — Now Available" % (prop.get("name", args.address), city)

    print("\n=== Step 7: Create MailerLite campaign ===")
    campaign_id = create_campaign(full_title, subject, html, args.segment, dry_run=args.dry_run)

    if campaign_id:
        print("\nDone! Campaign ID: %s" % campaign_id)
        print("Review at: https://app.mailerlite.com/campaigns")
    else:
        print("\nDry run complete.")

if __name__ == "__main__":
    main()
