"""Pure request semantics shared by image gating and shot selection.
Persona SSOT: data/persona_dossier.json.
"""
import re
from conversation_semantics import clauses, unquoted


def positive_text(text):
    """Remove negated alternatives, not 他拍/别人 or subsequent requests."""
    parts = []
    for part in clauses(unquoted(text or '')):
        part = re.sub(r'(?:不要|不用|不必|无需|别(?!人)).*?(?=但|而是|改为|改成|还是|$)', '', part)
        parts.append(part)
    return ' '.join(parts)
