"""Tools the answering model may call (lookups, status checks, ...), with a per-tenant allowlist.

Security rules, enforced here rather than trusted to the model:
- each tenant only sees and can call the tools on its allowlist;
- arguments are validated against a strict schema (no extra fields, so the model can't pass a tenant_id);
- the handler always receives the tenant from the calling code, never from the model;
- results are truncated, and errors come back as tool errors instead of exceptions.
"""

from __future__ import annotations

import sys

from collections.abc import Callable
from dataclasses import dataclass, field

MAX_RESULT_CHARS = 3000
_TYPES = {"string": str, "integer": int, "boolean": bool}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, dict]            # {"arg": {"type": "string", "description": "..."}}
    handler: Callable[[str, dict], str]    # (tenant_id, validated args) -> result text
    required: tuple[str, ...] = field(default=())

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "strict": True,
                "input_schema": {"type": "object", "properties": self.parameters,
                                 "required": list(self.required), "additionalProperties": False}}

    def validate(self, args: dict) -> str | None:
        """None if valid, else an error message."""
        if not isinstance(args, dict):
            return "arguments must be an object"
        if extra := set(args) - set(self.parameters):
            return f"unknown argument(s): {sorted(extra)}"
        if missing := [a for a in self.required if a not in args]:
            return f"missing argument(s): {missing}"
        for name, value in args.items():
            expected = _TYPES.get(self.parameters[name].get("type", "string"), str)
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                return f"argument {name!r} must be {self.parameters[name].get('type', 'string')}"
            if (choices := self.parameters[name].get("enum")) and value not in choices:
                return f"argument {name!r} must be one of {choices}"
        return None


class ToolRegistry:
    def __init__(self, tools: list[Tool], allowlist: dict[str, set[str]] | None = None):
        """`allowlist`: tenant_id -> allowed tool names. None = every tenant may use every tool."""
        self.tools = {t.name: t for t in tools}
        self.allowlist = allowlist

    def allowed(self, tenant_id: str) -> list[Tool]:
        names = set(self.tools) if self.allowlist is None else self.allowlist.get(tenant_id, set())
        return [t for n, t in self.tools.items() if n in names]

    def specs(self, tenant_id: str) -> list[dict]:
        return [t.spec() for t in self.allowed(tenant_id)]

    def call(self, tenant_id: str, name: str, args: dict) -> tuple[str, bool]:
        """(result text, is_error)."""
        tool = next((t for t in self.allowed(tenant_id) if t.name == name), None)
        if tool is None:
            return f"tool {name!r} is not available", True
        if error := tool.validate(args):
            return error, True
        try:
            result = tool.handler(tenant_id, args)
        except Exception as e:  # a failing tool must not crash the conversation, but it must not vanish either (D44)
            print(f"ERROR: tool {name} failed for tenant {tenant_id}: {type(e).__name__}: {e}", file=sys.stderr,
                  flush=True)
            return f"tool failed: {type(e).__name__}", True
        return str(result)[:MAX_RESULT_CHARS], False
