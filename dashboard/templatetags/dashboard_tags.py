from django import template

register = template.Library()


@register.filter
def get_unit_notes(notes_by_unit, unit_id):
    """Return list of notes for a given unit_id from notes_by_unit dict."""
    if not isinstance(notes_by_unit, dict):
        return []
    return notes_by_unit.get(unit_id, [])


@register.filter
def get_unit_notes_count(notes_by_unit, unit_id):
    """Return count of notes for a given unit_id."""
    if not isinstance(notes_by_unit, dict):
        return 0
    return len(notes_by_unit.get(unit_id, []))
