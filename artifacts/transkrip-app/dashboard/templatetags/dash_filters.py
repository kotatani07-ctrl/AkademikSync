"""Filter template kecil untuk dashboard eCampuz."""
from django import template

register = template.Library()


@register.filter
def fmt(value, spec="%.2f"):
    """Printf-style format: {{ nilai|fmt:'%.3f' }}"""
    try:
        return spec % value
    except (TypeError, ValueError):
        return str(value)


@register.filter
def lookup(d, key, default=""):
    """Ambil value dict dengan fallback: {{ dict|lookup:key }}"""
    if not d:
        return default
    return d.get(key, default)