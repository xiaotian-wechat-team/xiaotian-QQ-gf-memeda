"""Versioned role definitions. No runtime state, credentials or implicit role.

Transport bindings are globally unique within one environment. A relationship
may have several explicit bindings, but never inherits another relationship's
memory, model session or authorization.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ENVIRONMENTS = {"dev", "test", "prod"}


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"invalid stable identifier: {value!r}")
    return value


class Registry:
    def __init__(self, definitions, environment):
        if environment not in ENVIRONMENTS:
            raise ValueError("explicit dev/test/prod environment required")
        self.environment = environment
        self._roles = {}
        self._routes = {}
        for definition in definitions:
            role = copy.deepcopy(definition)
            if role.get("schema_version") != 2:
                raise ValueError("role schema_version must be 2")
            rid = identifier(role.get("role_id"))
            if rid in self._roles:
                raise ValueError(f"duplicate role: {rid}")
            if not isinstance(role.get("dossier"), dict) or not isinstance(role.get("workflows"), dict):
                raise ValueError("explicit dossier and workflows are required")
            relations = role.get("relationships")
            if not isinstance(relations, dict):
                raise ValueError("relationships must be a mapping")
            for relation_id, relation in relations.items():
                identifier(relation_id)
                if not isinstance(relation, dict) or not relation.get("kind"):
                    raise ValueError("explicit relationship kind required")
                for binding in relation.get("bindings", []):
                    if binding.get("environment") != environment:
                        continue
                    key = tuple(binding.get(k) for k in ("channel", "bot_account_id", "peer_type", "peer_id"))
                    if any(not isinstance(v, str) or not v or "*" in v for v in key):
                        raise ValueError("complete exact transport binding required")
                    if key in self._routes:
                        raise ValueError(f"ambiguous route: {key}")
                    self._routes[key] = (rid, relation_id)
            self._roles[rid] = role

    @classmethod
    def read(cls, root, environment):
        return cls([json.loads(p.read_text()) for p in sorted(Path(root).glob("*/role.json"))], environment)

    def role(self, role_id):
        return copy.deepcopy(self._roles[identifier(role_id)])

    def resolve(self, channel, bot_account_id, peer_type, peer_id):
        return self._routes.get((channel, bot_account_id, peer_type, peer_id))

    def select(self, role_ids):
        if not role_ids or len(set(role_ids)) != len(role_ids):
            raise ValueError("select explicit distinct role IDs")
        return [self.role(rid) for rid in role_ids]
