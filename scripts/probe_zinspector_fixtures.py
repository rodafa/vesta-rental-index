"""
Read-only probe — dump raw descriptions for zInspector test fixtures.

Requires Django. Run from project root:
    python manage.py shell < scripts/probe_zinspector_fixtures.py

Prints repr(wo.description) with no truncation for each target WO,
plus the most recent non-zInspector work order for a negative fixture.
"""

from maintenance.models import WorkOrder

TARGET_NUMBERS = ("100997", "101081", "100988")

print("=" * 80)
print("zInspector fixture probe")
print("=" * 80)

for num in TARGET_NUMBERS:
    try:
        wo = WorkOrder.objects.get(work_order_number=num)
        print(f"\n--- WO {num} (rentvine_id={wo.rentvine_id}) ---")
        print(repr(wo.description))
    except WorkOrder.DoesNotExist:
        print(f"\n--- WO {num}: NOT FOUND ---")

# Most recent non-zInspector WO
print("\n--- Most recent non-zInspector WO ---")
non_zi = (
    WorkOrder.objects.filter(is_active=True)
    .exclude(description__icontains="zinspector")
    .order_by("-source_created_at")
    .first()
)
if non_zi:
    print(f"WO {non_zi.work_order_number} (rentvine_id={non_zi.rentvine_id})")
    print(repr(non_zi.description))
else:
    print("No non-zInspector work orders found.")

print("\n" + "=" * 80)
print("Done. Paste the output above into the test fixtures.")
print("=" * 80)
