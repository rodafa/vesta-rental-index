import django, os, requests
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vesta_rental_index.settings")
django.setup()

import json

KEY = os.environ["MAILERLITE_API_KEY"]
HEADERS = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json", "Accept": "application/json"}
BASE = "https://connect.mailerlite.com/api"

# ---------------------------------------------------------------------------
# Image URLs — fresh RentVine token
# ---------------------------------------------------------------------------
# Permanent MailerLite CDN URLs (uploaded from RentVine CDN 2026-04-10)
HERO = "https://storage.mlcdn.com/account_image/921975/w2f0gbngvpPVJTxdt3i2YpXWO1xY2lZQB5dKwg0h.jpg"
PH1  = "https://storage.mlcdn.com/account_image/921975/umo6VqfpRAfXcRY4UV3zecWjko3krd3YlrNxsK5l.jpg"
PH2  = "https://storage.mlcdn.com/account_image/921975/milwv9vATEBxGbIseVn4oYfLdAT91GGr3pC0AsEp.jpg"
PH3  = "https://storage.mlcdn.com/account_image/921975/uicFmROq8Lj0WOLd2m5bwEtkBbsFJ5sqKgIa3Gwc.jpg"
PH4  = "https://storage.mlcdn.com/account_image/921975/xqoyhuIq4iB0bromYUmCOYuwLQhocrdlr3YO3l71.jpg"

# ---------------------------------------------------------------------------
# Load the base template and swap content
# ---------------------------------------------------------------------------
with open("/app/base_template.html", "r") as f:
    html = f.read()

swaps = [
    # Page title
    (
        "2BR / 1BA Candler Home with Fenced Yard!",
        "2BR / 2BA Apartment — Arden, NC"
    ),
    # Hero image
    (
        "https://storage.mlcdn.com/account_image/921975/P3Mnt0h0OESp0r0uSyuHJv1UJHPOeuaJgseXN2Kn.jpg",
        HERO
    ),
    # H1 headline
    (
        "2BR/1BA Home with Fenced Yard + Prime Candler Location",
        "Spacious 2BR/2BA Apartment in Arden, NC"
    ),
    # H4 subtitle (navy color)
    (
        "Older house, recently renovated close to everything",
        "Professionally Managed &bull; Minutes from Downtown Asheville"
    ),
    # "See full listing" button URL (appears twice — MSO + desktop)
    (
        'href="https://www.rentengine.io/listings/39485?accounts=c26fbfed-dd3d-4c91-a198-b4d3d8259712" target="_blank"',
        'href="https://www.rentengine.io/listings/50181?accounts=c26fbfed-dd3d-4c91-a198-b4d3d8259712" target="_blank"'
    ),
    # Left column photo 1
    (
        "https://storage.mlcdn.com/account_image/921975/EOc4QQ4iz1n6j9raYJyXRgBAVrZYh5wIgpVJqOqo.jpg",
        PH1
    ),
    # Left column photo 2
    (
        "https://storage.mlcdn.com/account_image/921975/sBsNgsySLULqm5ekmcmYN3Wr76tFfDSsna9FjepA.jpg",
        PH2
    ),
    # Left column photo 3
    (
        "https://storage.mlcdn.com/account_image/921975/nxEJGkjzbXEM0gdOTwjIma8B33q0OxN4Z6Jc0md9.jpg",
        PH3
    ),
    # Left column photo 4
    (
        "https://storage.mlcdn.com/account_image/921975/AxUHoWWS7LwVKeI48sue8zkNHUwRcGt0FclOSian.jpg",
        PH4
    ),
    # Bathrooms
    (
        "<strong>🛁 Bathrooms:</strong> 1",
        "<strong>🛁 Bathrooms:</strong> 2"
    ),
    # Square footage
    (
        "<strong>📐 Sq Ft:</strong> 1,106",
        "<strong>📐 Sq Ft:</strong> 947"
    ),
    # Address
    (
        "<strong>📍 Address:</strong> 78 Old Oak Hill Rd. Candler&nbsp;",
        "<strong>📍 Address:</strong> 110 Heywood Rd, Apt 9B, Arden NC 28704"
    ),
    # Rent
    (
        "<strong>💰 Rent:</strong> $1,550/month",
        "<strong>💰 Rent:</strong> $1,500/month"
    ),
    # Available date
    (
        "<strong>📅 Available:</strong>&nbsp;April 5, 2025",
        "<strong>📅 Available:</strong> Now"
    ),
    # Description text
    (
        "Located just off Starnes Cove Road, this residence offers the ideal gateway to everything West Asheville and Downtown have to offer while providing a private, peaceful retreat.<br><br>The Space Step inside to a generous 1,106 sq. ft. layout featuring a massive 21'0\" x 17'4\" living room, perfect for relaxing or hosting guests. The spacious eat-in kitchen and dining area (over 20' wide combined) provide ample room for home-cooked meals. Both bedrooms are well-proportioned, and the entire home has been updated within the last three years with modern finishes.<br><br>Outdoor Living This home truly shines outdoors! Enjoy a large private deck (over 27' long), a covered porch, and a dedicated patio area. The yard is fully fenced, offering security and privacy, complemented by a private driveway.<br>",
        (
            "Welcome to Apt. 9B at 110 Heywood Road &mdash; a beautifully maintained "
            "2-bedroom, 2-bathroom apartment nestled in a quiet Arden neighborhood, "
            "just minutes from the energy of Downtown Asheville, the Blue Ridge Parkway, "
            "and all that Western NC has to offer.<br><br>"
            "With 947 square feet of comfortable living space, two full bathrooms, and "
            "professional management that means every maintenance request gets handled "
            "fast &mdash; this is the kind of home you&rsquo;ll actually enjoy living in."
            "<br><br>"
            "Managed by Vesta Property Management, Asheville&rsquo;s trusted name in "
            "full-service residential care. We handle everything so you don&rsquo;t "
            "have to chase your landlord."
        )
    ),
    # "SCHEDULE VIEWING" button URL (appears twice — MSO + desktop)
    (
        'href="https://www.rentengine.io/listings/39485?accounts=c26fbfed-dd3d-4c91-a198-b4d3d8259712" target="_blank"',
        'href="https://www.rentengine.io/listings/50181?accounts=c26fbfed-dd3d-4c91-a198-b4d3d8259712" target="_blank"'
    ),
    # "SUBMIT APPLICATION" link — keep original BoomPay URL (same unit)
    (
        'href="https://screen.boompay.app/a/Oti6BjmKy1CLXrmcO9UF" target="_blank"',
        'href="https://screen.boompay.app/a/Oti6BjmKy1CLXrmcO9UF" target="_blank"'
    ),
    # "SCHEDULE VIEWING" button text — update emoji/text
    (
        "<div>👉 SCHEDULE VIEWING</div>",
        "<div>&#128205; SCHEDULE A SHOWING</div>"
    ),
    # "Got Property Management Questions?" banner — keep green, update text slightly
    (
        "Got Property Management Questions?",
        "Interested in This Home? Let&rsquo;s Connect."
    ),
]

for old, new in swaps:
    html = html.replace(old, new)

# Verify key replacements landed
checks = [
    ("110 Heywood", "address"),
    ("$1,500/month", "rent"),
    ("947", "sqft"),
    (PH1[:40], "photo 1"),
    (HERO[:40], "hero image"),
]
print("Replacement checks:")
for fragment, label in checks:
    found = fragment in html
    print("  [%s] %s" % ("OK" if found else "MISSING", label))

# ---------------------------------------------------------------------------
# Create new campaign + push content
# ---------------------------------------------------------------------------
print("\nCreating campaign...")
r_create = requests.post(BASE + "/campaigns", headers=HEADERS, json={
    "name": "110 Heywood Rd Apt 9B — Now Available",
    "type": "regular",
    "emails": [{
        "subject": "Now Available: 2BR/2BA in Arden, NC — $1,500/mo | Vesta",
        "from_name": "Vesta Property Management",
        "from": "support@vestapm.com",
    }],
})
print("Create status:", r_create.status_code)
if r_create.status_code not in (200, 201):
    print(r_create.text)
    exit(1)

new_id = r_create.json()["data"]["id"]
print("Campaign ID:", new_id)

print("Pushing content + segment...")
r_update = requests.put(BASE + "/campaigns/" + new_id, headers=HEADERS, json={
    "name": "110 Heywood Rd Apt 9B — Now Available",
    "type": "regular",
    "emails": [{
        "subject": "Now Available: 2BR/2BA in Arden, NC — $1,500/mo | Vesta",
        "from_name": "Vesta Property Management",
        "from": "support@vestapm.com",
        "content": html,
    }],
    "segments": ["129568169154054126"],
})
print("Update status:", r_update.status_code)
data = r_update.json()
if r_update.status_code == 200:
    missing = data.get("data", {}).get("missing_data", [])
    audience = data.get("data", {}).get("filter_for_humans", [])
    print("Missing data:", missing)
    print("Audience:", audience)
    print()
    print("Done! -> https://app.mailerlite.com/campaigns")
else:
    print(json.dumps(data, indent=2))
