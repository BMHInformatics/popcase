from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def sdd_help_config(context):
    from popcase.sdd_help import help_config
    geography = context.get('geographic_level') or getattr(context.get('form'), 'geographic_level', '')
    return help_config(geography, context.get('current_step'))

@register.filter
def get_item(d, k):
    """Safely get dict item in Django templates: {{ mydict|get_item:key }}"""
    if isinstance(d, dict):
        return d.get(k)
    return None
