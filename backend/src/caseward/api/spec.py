"""The OpenAPI specification as the source of truth for routing.

Each operation's roles, audit kind, parameters and schemas come from
docs/api, so the running API cannot drift from the published contract.
"""

import copy
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

DEFAULT_SPEC_DIR = Path(__file__).resolve().parents[4] / "docs" / "api"
METHODS = ("get", "post", "put", "patch", "delete")


@dataclass(frozen=True)
class Parameter:
    name: str
    location: str
    required: bool
    schema: dict[str, Any]


@dataclass
class Operation:
    operation_id: str
    method: str
    path: str
    roles: frozenset[str]
    audit: str
    provider_access: str | None
    special_category: bool
    parameters: list[Parameter]
    body_schema: dict[str, Any] | None
    body_required: bool
    response_schemas: dict[int, dict[str, Any] | None]
    validators: dict[str, Draft202012Validator] = field(default_factory=dict, repr=False)

    @property
    def idempotent(self) -> bool:
        return any(p.name == "Idempotency-Key" for p in self.parameters)

    @property
    def resource(self) -> str:
        """The entity type recorded on audit events, e.g. 'cases'."""
        return self.path.strip("/").split("/")[0]

    def validator(self, key: str, schema: dict[str, Any], formats: FormatChecker) -> Draft202012Validator:
        if key not in self.validators:
            self.validators[key] = Draft202012Validator(schema, format_checker=formats)
        return self.validators[key]


class Spec:
    def __init__(self, spec_dir: Path, name: str):
        self.spec_dir = spec_dir
        self.name = name
        self.document = self._load(name)
        self.operations = list(self._operations())

    @cache
    def _load(self, name: str) -> dict[str, Any]:
        return yaml.safe_load((self.spec_dir / name).read_text())

    def deref(self, node: Any, current: str) -> Any:
        """Inline every $ref. The schemas have no cycles, so this terminates."""
        if isinstance(node, list):
            return [self.deref(v, current) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            file_part, _, pointer = node["$ref"].partition("#")
            target_file = file_part or current
            target: Any = self._load(target_file)
            for part in pointer.strip("/").split("/"):
                target = target[part]
            resolved = copy.deepcopy(self.deref(target, target_file))
            siblings = {k: self.deref(v, current) for k, v in node.items() if k != "$ref"}
            return {**resolved, **siblings} if isinstance(resolved, dict) else resolved
        return {k: self.deref(v, current) for k, v in node.items()}

    def _operations(self):
        for path, item in self.document["paths"].items():
            for method in METHODS:
                if method not in item:
                    continue
                op = self.deref(item[method], self.name)
                body = op.get("requestBody")
                body_schema = None
                if body:
                    content = body["content"]
                    body_schema = next(iter(content.values()))["schema"]
                responses = {}
                for status, response in op["responses"].items():
                    content = response.get("content", {})
                    json_content = content.get("application/json")
                    responses[int(status)] = json_content["schema"] if json_content else None
                yield Operation(
                    operation_id=op["operationId"],
                    method=method,
                    path=path,
                    roles=frozenset(op["x-roles"]),
                    audit=op["x-audit"],
                    provider_access=op.get("x-provider-access"),
                    special_category=bool(op.get("x-special-category")),
                    parameters=[Parameter(p["name"], p["in"], p.get("required", False), p.get("schema", {}))
                                for p in op.get("parameters", [])],
                    body_schema=body_schema,
                    body_required=bool(body and body.get("required")),
                    response_schemas=responses,
                )


def load_spec(kind: str, spec_dir: Path | None = None) -> Spec:
    return Spec(spec_dir or DEFAULT_SPEC_DIR, {"client": "client.yaml", "operations": "operations.yaml"}[kind])
