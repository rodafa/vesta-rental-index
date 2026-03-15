"""Send listing announcement and tenant referral emails via SendGrid."""
import html as html_module
import logging
import os
import re
from datetime import datetime, timedelta

import requests

_STATE_ABBREVS = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


def _zillow_listing_url(addr):
    """Build a Zillow listing search URL from address components."""
    if not isinstance(addr, dict):
        return ""
    number = addr.get("street_number") or ""
    street = addr.get("street_name") or ""
    city = addr.get("city") or ""
    state_full = addr.get("state") or ""
    state = _STATE_ABBREVS.get(state_full, state_full[:2] if len(state_full) > 2 else state_full)
    slug = "-".join(p for p in [number, street, city, state] if p)
    slug = re.sub(r"[^\w-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return "https://www.zillow.com/homes/%s_rb/" % slug

logger = logging.getLogger(__name__)

SENDGRID_BASE = "https://api.sendgrid.com/v3"
CHUNK_SIZE = 1000
SHOW_URL = "https://vestapm.com/homes-for-rent"

# Brand colors (matches owner_report.html)
NAVY = "#1E3D58"
BLUE = "#6EA5CD"
GREEN = "#1a7a28"
GREEN_BG = "#d6f0da"
BG = "#EFF5F9"


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def _build_listing_context(listing):
    """Normalize a RentEngine unit dict into display-ready fields."""
    addr = listing.get("address") or {}
    if isinstance(addr, dict):
        formatted = addr.get("formatted_address") or ""
        city = addr.get("city") or ""
        state = addr.get("state") or ""
        zipcode = addr.get("zip_code") or ""
    else:
        formatted = str(addr)
        city = state = zipcode = ""

    line2 = "%s, %s %s" % (city, state, zipcode) if city else ""

    photos = [
        p["original"]
        for p in (listing.get("marketing_photos") or [])
        if not p.get("hidden") and p.get("original")
    ]

    raw_rent = (
        listing.get("target_rental_rate")
        or listing.get("rent")
        or listing.get("price")
        or 0
    )
    try:
        rent = "{:,.0f}".format(float(raw_rent))
    except (ValueError, TypeError):
        rent = str(raw_rent)

    available = listing.get("earliest_move_in_date") or ""
    if available:
        try:
            dt = datetime.strptime(available, "%Y-%m-%d")
            available = dt.strftime("%-m/%-d")
        except Exception:
            pass
    available = available or "Now"

    description = listing.get("marketing_description") or ""

    # Opening paragraph — first non-boilerplate paragraph
    paragraphs = [
        p.strip()
        for p in description.split("\n\n")
        if p.strip() and not p.strip().startswith("***") and "-->" not in p
    ]
    snippet = paragraphs[0] if paragraphs else ""
    if len(snippet) > 600:
        snippet = snippet[:597] + "..."
    snippet = html_module.escape(snippet).replace("\n", "<br>")

    # Feature bullets — pull --> lines, skip boilerplate sections
    _SKIP_SECTIONS = ("showing", "application", "screening", "need help",
                      "questions", "by submitting", "utilities", "terms")
    bullets = []
    _skip = False
    for line in description.split("\n"):
        stripped = line.strip()
        lower = stripped.lower()
        if any(s in lower for s in _SKIP_SECTIONS) and (lower.endswith(":") or lower.endswith("& terms:")):
            _skip = True
        if _skip:
            continue
        if stripped.startswith("-->"):
            bullet = stripped[3:].strip()
            if bullet and len(bullets) < 8:
                bullets.append(bullet)

    amenities_raw = listing.get("highlighted_amenities") or ""
    amenities_extra = [a.strip() for a in amenities_raw.split(",") if a.strip()] if amenities_raw else []
    # Merge: parsed bullets take priority, fall back to highlighted_amenities
    amenities = bullets if bullets else amenities_extra

    raw_deposit = listing.get("security_deposit_amount")
    deposit = ""
    if raw_deposit:
        try:
            deposit = "${:,.0f}".format(float(raw_deposit))
        except (ValueError, TypeError):
            pass

    sqft_raw = listing.get("sqft")
    sqft = "{:,}".format(int(sqft_raw)) if sqft_raw else ""

    # Listing click-through URL: Zillow listing page, fallback to RentVine
    extracted_from = listing.get("extracted_from") or ""
    rentvine_listing_url = extracted_from.replace("/api/public/listings/", "/listings/") if extracted_from else ""
    listing_url = _zillow_listing_url(addr) or rentvine_listing_url or SHOW_URL

    return {
        "formatted_address": html_module.escape(formatted),
        "line2": html_module.escape(line2),
        "city": html_module.escape(city),
        "state": html_module.escape(state),
        "zip_code": html_module.escape(zipcode),
        "property_type": html_module.escape((listing.get("property_type") or "Home").title()),
        "bedrooms": str(listing.get("bedrooms") or ""),
        "bathrooms": str(listing.get("bathrooms") or ""),
        "sqft": sqft,
        "rent": rent,
        "available": available,
        "pets": html_module.escape(str(listing.get("pets_allowed") or "—")),
        "laundry": html_module.escape(str(listing.get("laundry") or "—")),
        "parking": html_module.escape(", ".join(listing.get("parking_type") or []) or "—"),
        "amenities": amenities,
        "snippet": snippet,
        "photos": photos,
        "hero": photos[0] if photos else "",
        "extra_photos": photos[1:3],
        "apply_url": listing.get("custom_application_url") or SHOW_URL,
        "show_url": SHOW_URL,
        "listing_url": listing_url,
        "deposit": deposit,
    }


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _photo_grid(extra_photos, listing_url):
    if not extra_photos:
        return ""
    cells = ""
    for i, photo in enumerate(extra_photos[:2]):
        pad = "padding-right:2px;" if i == 0 else "padding-left:2px;"
        cells += (
            '<td style="%(pad)s" width="50%%">'
            '<a href="%(url)s" target="_blank" style="display:block;line-height:0;">'
            '<img src="%(src)s" width="284" alt="" '
            'style="display:block;width:100%%;height:200px;object-fit:cover;">'
            '</a>'
            '</td>'
        ) % {"pad": pad, "src": photo, "url": listing_url}
    return (
        '<tr><td style="padding:0;line-height:0;">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">'
        '<tr>%(cells)s</tr></table>'
        '</td></tr>'
    ) % {"cells": cells}


def _highlights_section(amenities):
    if not amenities:
        return ""
    items = "".join(
        '<li style="margin:0 0 8px;font-size:14px;color:#555;line-height:1.5;">%s</li>'
        % html_module.escape(a)
        for a in amenities[:6]
    )
    return (
        '<tr><td style="background:#ffffff;padding:0 32px 24px;">'
        '<p style="margin:0 0 12px;font-size:16px;font-weight:700;color:%(navy)s;'
        'font-family:Helvetica,Arial,sans-serif;">Highlights</p>'
        '<ul style="margin:0;padding-left:20px;">%(items)s</ul>'
        '</td></tr>'
    ) % {"navy": NAVY, "items": items}


def _announcement_html(ctx):
    hero_img = (
        '<a href="%(url)s" target="_blank" style="display:block;line-height:0;">'
        '<img src="%(src)s" width="600" alt="%(alt)s" '
        'style="display:block;width:100%%;height:auto;max-height:400px;object-fit:cover;">'
        '</a>'
        % {"src": ctx["hero"], "alt": ctx["formatted_address"], "url": ctx["listing_url"]}
    ) if ctx["hero"] else ""

    deposit_row = (
        '<tr><td colspan="3" style="padding:4px 0;font-size:13px;color:#555;">'
        '<strong style="color:%(navy)s;">Security Deposit:</strong> %(dep)s'
        '</td></tr>'
    ) % {"navy": NAVY, "dep": ctx["deposit"]} if ctx["deposit"] else ""

    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>New Listing Available</title>
</head>
<body style="margin:0;padding:0;background:%(bg)s;font-family:Helvetica,Arial,sans-serif;">

<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%(bg)s;">
<tr><td align="center" style="padding:24px 12px;">

<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%%;">

<!-- HEADER -->
<tr><td style="background:%(navy)s;padding:20px 32px;border-radius:8px 8px 0 0;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td>
      <p style="margin:0;color:#ffffff;font-size:22px;font-weight:800;letter-spacing:2px;font-family:Helvetica,Arial,sans-serif;">VESTA</p>
      <p style="margin:2px 0 0;color:rgba(255,255,255,.65);font-size:11px;letter-spacing:.5px;font-family:Helvetica,Arial,sans-serif;">PROPERTY MANAGEMENT</p>
    </td>
    <td align="right" style="vertical-align:middle;">
      <span style="display:inline-block;background:%(green)s;color:#fff;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;padding:5px 14px;border-radius:20px;">New Listing</span>
    </td>
  </tr></table>
</td></tr>

<!-- HERO IMAGE -->
<tr><td style="padding:0;line-height:0;">%(hero_img)s</td></tr>

<!-- ADDRESS + PRICE BANNER -->
<tr><td style="background:%(navy)s;padding:22px 32px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="vertical-align:top;padding-right:16px;">
      <p style="margin:0 0 3px;color:%(blue)s;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;">%(property_type)s</p>
      <h1 style="margin:0;color:#fff;font-size:24px;font-weight:800;line-height:1.2;font-family:Helvetica,Arial,sans-serif;">%(formatted_address)s</h1>
      <p style="margin:5px 0 0;color:rgba(255,255,255,.65);font-size:14px;">%(line2)s</p>
    </td>
    <td align="right" style="vertical-align:middle;white-space:nowrap;">
      <p style="margin:0;color:rgba(255,255,255,.55);font-size:11px;text-align:right;letter-spacing:.5px;text-transform:uppercase;">Per Month</p>
      <p style="margin:3px 0 0;color:#fff;font-size:36px;font-weight:800;line-height:1;text-align:right;font-family:Helvetica,Arial,sans-serif;">$%(rent)s</p>
    </td>
  </tr></table>
</td></tr>

<!-- STATS ROW -->
<tr><td style="background:#fff;border-bottom:1px solid #e2eaf0;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td align="center" style="padding:22px 8px;border-right:1px solid #e2eaf0;width:33%%;">
      <p style="margin:0;font-size:28px;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(bedrooms)s</p>
      <p style="margin:5px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Beds</p>
    </td>
    <td align="center" style="padding:22px 8px;border-right:1px solid #e2eaf0;width:33%%;">
      <p style="margin:0;font-size:28px;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(bathrooms)s</p>
      <p style="margin:5px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Baths</p>
    </td>
    <td align="center" style="padding:22px 8px;width:33%%;">
      <p style="margin:0;font-size:%(sqft_size)s;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(sqft)s</p>
      <p style="margin:5px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Sq Ft</p>
    </td>
  </tr></table>
</td></tr>

%(photo_grid)s

<!-- DESCRIPTION -->
<tr><td style="background:#fff;padding:28px 32px 20px;">
  <p style="margin:0 0 10px;font-size:16px;font-weight:700;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">About This Home</p>
  <p style="margin:0;font-size:14px;line-height:1.8;color:#555;">%(snippet)s</p>
</td></tr>

%(highlights)s

<!-- DETAILS STRIP -->
<tr><td style="background:#f5f8fb;border-top:1px solid #e2eaf0;border-bottom:1px solid #e2eaf0;padding:16px 32px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td width="33%%" style="padding:5px 12px 5px 0;font-size:13px;color:#555;vertical-align:top;">
        <strong style="color:%(navy)s;display:block;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px;">Pets</strong>%(pets)s
      </td>
      <td width="33%%" style="padding:5px 12px;font-size:13px;color:#555;vertical-align:top;">
        <strong style="color:%(navy)s;display:block;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px;">Laundry</strong>%(laundry)s
      </td>
      <td width="33%%" style="padding:5px 0 5px 12px;font-size:13px;color:#555;vertical-align:top;">
        <strong style="color:%(navy)s;display:block;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:2px;">Parking</strong>%(parking)s
      </td>
    </tr>
    %(deposit_row)s
  </table>
</td></tr>

<!-- CTA BUTTONS -->
<tr><td style="background:#fff;padding:32px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="padding-right:8px;" width="50%%">
      <a href="%(listing_url)s"
         style="display:block;text-align:center;background:%(green)s;color:#fff;text-decoration:none;
                font-size:15px;font-weight:700;padding:16px 12px;border-radius:6px;
                font-family:Helvetica,Arial,sans-serif;">
        View Full Listing
      </a>
    </td>
    <td style="padding-left:8px;" width="50%%">
      <a href="%(apply_url)s"
         style="display:block;text-align:center;background:#fff;color:%(navy)s;text-decoration:none;
                font-size:15px;font-weight:700;padding:15px 12px;border-radius:6px;
                border:2px solid %(navy)s;font-family:Helvetica,Arial,sans-serif;">
        Apply Now
      </a>
    </td>
  </tr></table>
</td></tr>

<!-- FOOTER -->
<tr><td style="background:%(bg)s;border-top:1px solid #d0dce8;padding:24px 32px;border-radius:0 0 8px 8px;text-align:center;">
  <p style="margin:0;font-size:13px;color:#667788;">
    Questions? Call or text
    <a href="tel:+18288822707" style="color:%(navy)s;text-decoration:none;font-weight:600;">(828) 882-2707</a>
    &nbsp;&middot;&nbsp;
    <a href="mailto:hello@vestapm.com" style="color:%(navy)s;text-decoration:none;font-weight:600;">hello@vestapm.com</a>
  </p>
  <p style="margin:8px 0 0;font-size:12px;color:#aaa;">&copy; 2026 Vesta Property Management &middot; Asheville, NC</p>
  <p style="margin:8px 0 0;font-size:11px;"><a href="https://vestapm.com/unsubscribe" style="color:#bbb;text-decoration:none;">Unsubscribe</a></p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
    ) % {
        "bg": BG,
        "navy": NAVY,
        "blue": BLUE,
        "green": GREEN,
        "hero_img": hero_img,
        "property_type": ctx["property_type"],
        "formatted_address": ctx["formatted_address"],
        "line2": ctx["line2"],
        "rent": ctx["rent"],
        "bedrooms": ctx["bedrooms"],
        "bathrooms": ctx["bathrooms"],
        "sqft": ctx["sqft"],
        "sqft_size": "22px" if ctx["sqft"] else "28px",
        "photo_grid": _photo_grid(ctx["extra_photos"], ctx["listing_url"]),
        "snippet": ctx["snippet"],
        "highlights": _highlights_section(ctx["amenities"]),
        "pets": ctx["pets"],
        "laundry": ctx["laundry"],
        "parking": ctx["parking"],
        "deposit_row": deposit_row,
        "show_url": ctx["show_url"],
        "listing_url": ctx["listing_url"],
        "apply_url": ctx["apply_url"],
    }


def _referral_html(ctx):
    """Build the tenant referral email. Uses -first_name- substitution token."""
    hero_img = (
        '<a href="%(url)s" target="_blank" style="display:block;line-height:0;">'
        '<img src="%(src)s" width="536" alt="%(alt)s" '
        'style="display:block;width:100%%;height:auto;max-height:260px;object-fit:cover;'
        'border-radius:6px 6px 0 0;">'
        '</a>'
        % {"src": ctx["hero"], "alt": ctx["formatted_address"], "url": ctx["listing_url"]}
    ) if ctx["hero"] else ""

    return (
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Know someone looking?</title>
</head>
<body style="margin:0;padding:0;background:%(bg)s;font-family:Helvetica,Arial,sans-serif;">

<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0" style="background:%(bg)s;">
<tr><td align="center" style="padding:24px 12px;">

<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%%;">

<!-- HEADER -->
<tr><td style="background:%(navy)s;padding:20px 32px;border-radius:8px 8px 0 0;">
  <p style="margin:0;color:#ffffff;font-size:22px;font-weight:800;letter-spacing:2px;font-family:Helvetica,Arial,sans-serif;">VESTA</p>
  <p style="margin:2px 0 0;color:rgba(255,255,255,.65);font-size:11px;letter-spacing:.5px;font-family:Helvetica,Arial,sans-serif;">PROPERTY MANAGEMENT</p>
</td></tr>

<!-- GREETING -->
<tr><td style="background:#fff;padding:32px 32px 24px;">
  <p style="margin:0 0 8px;font-size:20px;font-weight:700;color:#1a1a1a;font-family:Helvetica,Arial,sans-serif;">Hi -first_name-,</p>
  <p style="margin:0;font-size:15px;line-height:1.7;color:#555;">
    We just listed a beautiful new home and we need your help spreading the word!
    Forward this email to <strong>every friend, family member, or coworker</strong> you know
    who might be looking &mdash; the more people you tell, the better your chances of earning
    <strong style="color:%(green)s;">$200 off your next month&#39;s rent.</strong>
  </p>
</td></tr>

<!-- REFERRAL OFFER BOX -->
<tr><td style="background:#fff;padding:0 32px 28px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"
         style="background:%(green_bg)s;border-left:4px solid %(green)s;border-radius:4px;padding:0;">
    <tr><td style="padding:18px 20px;">
      <p style="margin:0 0 4px;font-size:13px;font-weight:700;color:%(green)s;text-transform:uppercase;letter-spacing:1px;">Here&#39;s How It Works</p>
      <p style="margin:0;font-size:20px;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">Forward this email &rarr; They sign &rarr; You save $200</p>
      <p style="margin:10px 0 0;font-size:14px;color:#445;line-height:1.7;">
        Simply hit <strong>Forward</strong> and send this to as many people as possible.
        When someone you referred signs a lease, we&#39;ll automatically credit
        <strong>$200 off your next rent payment</strong>. No forms, no hassle &mdash;
        just have them mention your name when they apply.
      </p>
    </td></tr>
  </table>
</td></tr>

<!-- LISTING PREVIEW CARD -->
<tr><td style="background:#fff;padding:0 32px 32px;">
  <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"
         style="border:1px solid #d0dce8;border-radius:8px;overflow:hidden;">

    <!-- Photo -->
    <tr><td style="padding:0;line-height:0;">%(hero_img)s</td></tr>

    <!-- Address + price -->
    <tr><td style="background:%(navy)s;padding:16px 20px;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="vertical-align:top;">
          <p style="margin:0 0 2px;color:%(blue)s;font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;">%(property_type)s</p>
          <p style="margin:0;color:#fff;font-size:18px;font-weight:800;font-family:Helvetica,Arial,sans-serif;">%(formatted_address)s</p>
          <p style="margin:3px 0 0;color:rgba(255,255,255,.65);font-size:13px;">%(line2)s</p>
        </td>
        <td align="right" style="vertical-align:middle;white-space:nowrap;padding-left:12px;">
          <p style="margin:0;color:rgba(255,255,255,.55);font-size:11px;text-align:right;">per month</p>
          <p style="margin:2px 0 0;color:#fff;font-size:28px;font-weight:800;line-height:1;font-family:Helvetica,Arial,sans-serif;">$%(rent)s</p>
        </td>
      </tr></table>
    </td></tr>

    <!-- Stats -->
    <tr><td style="background:#fff;border-bottom:1px solid #e8ecf0;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td align="center" style="padding:14px 6px;border-right:1px solid #e8ecf0;width:33%%;">
          <p style="margin:0;font-size:20px;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(bedrooms)s</p>
          <p style="margin:3px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Beds</p>
        </td>
        <td align="center" style="padding:14px 6px;border-right:1px solid #e8ecf0;width:33%%;">
          <p style="margin:0;font-size:20px;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(bathrooms)s</p>
          <p style="margin:3px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Baths</p>
        </td>
        <td align="center" style="padding:14px 6px;width:33%%;">
          <p style="margin:0;font-size:%(sqft_size)s;font-weight:800;color:%(navy)s;font-family:Helvetica,Arial,sans-serif;">%(sqft)s</p>
          <p style="margin:3px 0 0;font-size:10px;font-weight:700;color:#aaa;text-transform:uppercase;letter-spacing:1px;">Sq Ft</p>
        </td>
      </tr></table>
    </td></tr>

    <!-- CTA buttons inside card -->
    <tr><td style="background:#fff;padding:20px;">
      <table role="presentation" width="100%%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="padding-right:6px;" width="50%%">
          <a href="%(listing_url)s"
             style="display:block;text-align:center;background:%(green)s;color:#fff;text-decoration:none;
                    font-size:14px;font-weight:700;padding:14px 8px;border-radius:6px;
                    font-family:Helvetica,Arial,sans-serif;">
            View Full Listing
          </a>
        </td>
        <td style="padding-left:6px;" width="50%%">
          <a href="%(apply_url)s"
             style="display:block;text-align:center;background:#fff;color:%(navy)s;text-decoration:none;
                    font-size:14px;font-weight:700;padding:13px 8px;border-radius:6px;
                    border:2px solid %(navy)s;font-family:Helvetica,Arial,sans-serif;">
            Apply Now
          </a>
        </td>
      </tr></table>
    </td></tr>

  </table>
</td></tr>

<!-- FOOTER -->
<tr><td style="background:%(bg)s;border-top:1px solid #d0dce8;padding:24px 32px;border-radius:0 0 8px 8px;text-align:center;">
  <p style="margin:0;font-size:13px;color:#667788;">
    Questions?
    <a href="tel:+18288822707" style="color:%(navy)s;text-decoration:none;font-weight:600;">(828) 882-2707</a>
    &nbsp;&middot;&nbsp;
    <a href="mailto:support@vestapm.com" style="color:%(navy)s;text-decoration:none;font-weight:600;">support@vestapm.com</a>
  </p>
  <p style="margin:8px 0 0;font-size:12px;color:#aaa;">&copy; 2026 Vesta Property Management &middot; Asheville, NC</p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""
    ) % {
        "bg": BG,
        "navy": NAVY,
        "blue": BLUE,
        "green": GREEN,
        "green_bg": GREEN_BG,
        "hero_img": hero_img,
        "property_type": ctx["property_type"],
        "formatted_address": ctx["formatted_address"],
        "line2": ctx["line2"],
        "rent": ctx["rent"],
        "bedrooms": ctx["bedrooms"],
        "bathrooms": ctx["bathrooms"],
        "sqft": ctx["sqft"],
        "sqft_size": "18px" if ctx["sqft"] else "20px",
        "show_url": ctx["show_url"],
        "listing_url": ctx["listing_url"],
        "apply_url": ctx["apply_url"],
    }


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------

def send_listing_announcement(listing):
    """
    Fetch all MailerLite subscribers and send a listing announcement to each
    via POST /v3/mail/send with personalizations (batched in chunks of 1000).
    Returns True if at least one batch succeeded, False on total failure.
    """
    from leasing.services.mailerlite_sync import fetch_mailerlite_subscribers

    sendgrid_key = os.environ["SENDGRID_API_KEY"]
    from_email = os.environ.get("SENDGRID_FROM_EMAIL_ANNOUNCEMENTS", "hello@vestapm.com")
    from_name = os.environ.get("SENDGRID_FROM_NAME", "Vesta Property Management")

    ctx = _build_listing_context(listing)

    try:
        subscribers = fetch_mailerlite_subscribers()
    except Exception:
        logger.exception("Failed to fetch MailerLite subscribers for announcement")
        return False

    personalizations = [
        {"to": [{"email": s["email"]}]}
        for s in subscribers
        if s.get("email")
    ]

    if not personalizations:
        logger.warning("No MailerLite subscribers found — skipping announcement")
        return False

    subject = "New Listing: %s — $%s/mo" % (ctx["formatted_address"], ctx["rent"])
    html_body = _announcement_html(ctx)
    headers = {
        "Authorization": "Bearer %s" % sendgrid_key,
        "Content-Type": "application/json",
    }
    any_success = False

    for i in range(0, len(personalizations), CHUNK_SIZE):
        chunk = personalizations[i:i + CHUNK_SIZE]
        try:
            resp = requests.post(
                "%s/mail/send" % SENDGRID_BASE,
                headers=headers,
                json={
                    "personalizations": chunk,
                    "from": {"email": from_email, "name": from_name},
                    "subject": subject,
                    "content": [{"type": "text/html", "value": html_body}],
                },
                timeout=60,
            )
            resp.raise_for_status()
            any_success = True
            logger.info(
                "Listing announcement batch %d-%d sent for %s",
                i + 1, i + len(chunk), ctx["formatted_address"],
            )
        except Exception:
            logger.exception(
                "Failed to send announcement batch %d-%d for %s",
                i + 1, i + len(chunk), ctx["formatted_address"],
            )

    return any_success


def send_tenant_referral_blast(listing):
    """
    Send a personalized referral email to all active tenants.
    Returns True on success, False on failure.
    """
    from django.utils import timezone
    from integrations.models import APISyncLog
    from leasing.models import Tenant

    sendgrid_key = os.environ["SENDGRID_API_KEY"]
    from_email = os.environ.get("SENDGRID_FROM_EMAIL_REFERRALS", "support@vestapm.com")
    from_name = os.environ.get("SENDGRID_FROM_NAME", "Vesta Property Management")

    ctx = _build_listing_context(listing)

    # Check if local tenant data is fresh enough
    tenants = list(
        Tenant.objects.filter(
            lease__primary_lease_status=2
        ).distinct().values("name", "first_name", "email")
    )

    log = APISyncLog.objects.filter(
        source="rentvine", endpoint="leases/search", status="completed"
    ).order_by("-completed_at").first()
    stale = not log or log.completed_at < timezone.now() - timedelta(hours=24)

    if len(tenants) == 0 or stale:
        logger.info("Tenant data stale or empty — falling back to RentVine API")
        try:
            from integrations.rentvine.client import RentvineClient
            client = RentvineClient()
            leases = client.get_all("/leases/search")
            active_leases = [l for l in leases if l.get("primary_lease_status") == 2]
            tenants = []
            for lease in active_leases:
                lease_id = lease.get("id") or lease.get("leaseId")
                if not lease_id:
                    continue
                try:
                    lease_tenants = client.get("/leases/%s/tenants" % lease_id)
                    if isinstance(lease_tenants, list):
                        for t in lease_tenants:
                            email = t.get("email", "")
                            name = t.get("name", "") or t.get("fullName", "")
                            first_name = t.get("firstName", "") or (name.split()[0] if name else "")
                            if email:
                                tenants.append({"name": name, "first_name": first_name, "email": email})
                except Exception:
                    logger.exception("Could not fetch tenants for lease %s", lease_id)
        except Exception:
            logger.exception("RentVine fallback failed for referral blast")

    if not tenants:
        logger.warning("No tenants found for referral blast — skipping")
        return False

    personalizations = []
    for tenant in tenants:
        email = tenant.get("email", "").strip()
        if not email:
            continue
        first_name = (
            tenant.get("first_name")
            or (tenant.get("name") or "").split()[0]
            or "Resident"
        )
        personalizations.append({
            "to": [{"email": email}],
            "substitutions": {"-first_name-": first_name},
        })

    if not personalizations:
        logger.warning("No valid tenant emails for referral blast")
        return False

    html_body = _referral_html(ctx)

    try:
        resp = requests.post(
            "%s/mail/send" % SENDGRID_BASE,
            headers={
                "Authorization": "Bearer %s" % sendgrid_key,
                "Content-Type": "application/json",
            },
            json={
                "personalizations": personalizations,
                "from": {"email": from_email, "name": from_name},
                "subject": "Know someone looking for a home? Earn $200 off rent.",
                "content": [{"type": "text/html", "value": html_body}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        logger.info(
            "Tenant referral blast sent to %d tenants for listing %s",
            len(personalizations), ctx["formatted_address"],
        )
        return True
    except Exception:
        logger.exception("Failed to send tenant referral blast for %s", ctx["formatted_address"])
        return False
