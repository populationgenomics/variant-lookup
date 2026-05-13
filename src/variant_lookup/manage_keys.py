"""CLI for managing the api-keys.yaml file.

Usage::

    uv run python -m variant_lookup.manage_keys add <name>      # generates a token
    uv run python -m variant_lookup.manage_keys revoke <name>   # removes a key
    uv run python -m variant_lookup.manage_keys list            # lists names

The keys file path is read from ``API_KEYS_FILE`` in ``.env`` (or the
environment), matching the runtime setting.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import yaml
from argon2 import PasswordHasher

_hasher = PasswordHasher()


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"keys": []}
    return yaml.safe_load(path.read_text()) or {"keys": []}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def cmd_add(path: Path, name: str) -> int:
    data = _load(path)
    entries: list[dict[str, str]] = data.setdefault("keys", [])
    if any(entry["name"] == name for entry in entries):
        print(f"key '{name}' already exists; revoke it first", file=sys.stderr)
        return 1
    secret = secrets.token_urlsafe(32)
    entries.append({"name": name, "hash": _hasher.hash(secret)})
    _save(path, data)
    print(f"created key '{name}'. Save this token — it won't be shown again:")
    print(f"  {name}.{secret}")
    return 0


def cmd_revoke(path: Path, name: str) -> int:
    data = _load(path)
    entries: list[dict[str, str]] = data.get("keys", [])
    new_entries = [entry for entry in entries if entry["name"] != name]
    if len(new_entries) == len(entries):
        print(f"no key named '{name}'", file=sys.stderr)
        return 1
    data["keys"] = new_entries
    _save(path, data)
    print(f"revoked key '{name}'")
    return 0


def cmd_list(path: Path) -> int:
    data = _load(path)
    entries: list[dict[str, str]] = data.get("keys", [])
    if not entries:
        print("(no keys configured)")
        return 0
    for entry in entries:
        print(entry["name"])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage_keys", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=Path,
        default=Path(os.environ.get("API_KEYS_FILE", "api-keys.yaml")),
        help="path to api-keys.yaml (default: $API_KEYS_FILE)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="generate and append a new key")
    add.add_argument("name", help="caller name")

    revoke = subparsers.add_parser("revoke", help="remove a key by name")
    revoke.add_argument("name", help="caller name")

    subparsers.add_parser("list", help="list configured key names")

    args = parser.parse_args(argv)
    if args.command == "add":
        return cmd_add(args.file, args.name)
    if args.command == "revoke":
        return cmd_revoke(args.file, args.name)
    if args.command == "list":
        return cmd_list(args.file)
    return 2


if __name__ == "__main__":
    sys.exit(main())
