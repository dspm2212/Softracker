"""CLI tool for generating and rotating shared authentication tokens.

Generates cryptographically random tokens, stores their SHA-256 hashes
in tokens.yaml, and prints plaintext tokens once to the console.

Usage:
    python scripts/generar_tokens.py --shared
    python scripts/generar_tokens.py --rotar-shared --gracia-horas 24
    python scripts/generar_tokens.py --admin

Author: Daniel Perez
"""

from __future__ import annotations

import argparse
import base64
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.auth import hash_token  # noqa: E402

_DEFAULT_TOKENS_FILE = _PROJECT_ROOT / "tokens.yaml"
_DEFAULT_CONFIG_FILE = _PROJECT_ROOT / "config.yaml"


def _generate_token() -> str:
    """Return a 36-byte cryptographically random base64url token (~48 chars)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(36)).decode("utf-8").rstrip("=")


def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_yaml(path: Path) -> dict:
    if path.exists():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {}


def _save_yaml(data: dict, path: Path) -> None:
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )


def cmd_shared(tokens_file: Path) -> None:
    """Generate a fresh shared token for every room."""
    data = _load_yaml(tokens_file)
    token = _generate_token()
    data["shared_token"] = {
        "active_hash": hash_token(token),
        "active_since": _now_utc_str(),
        "previous_hash": None,
        "previous_until": None,
    }
    data.pop("rooms", None)

    _save_yaml(data, tokens_file)

    print("\n=== NEW SHARED AGENT TOKEN ===")
    print("Store this plaintext token securely - it will NOT be shown again.\n")
    print(f"  Shared token: {token}")
    print(f"\nHashes written to: {tokens_file}")


def cmd_rotar_shared(gracia_horas: int, tokens_file: Path) -> None:
    """Rotate the shared token, keeping the old one valid temporarily."""
    data = _load_yaml(tokens_file)
    shared_token: dict | None = data.get("shared_token")

    if not shared_token:
        print(f"ERROR: shared_token not found in {tokens_file}", file=sys.stderr)
        sys.exit(1)

    old_active_hash = shared_token.get("active_hash")
    new_token = _generate_token()
    grace_until = (
        datetime.now(timezone.utc) + timedelta(hours=gracia_horas)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    data["shared_token"] = {
        "active_hash": hash_token(new_token),
        "active_since": _now_utc_str(),
        "previous_hash": old_active_hash,
        "previous_until": grace_until,
    }

    _save_yaml(data, tokens_file)

    print("\n=== SHARED TOKEN ROTATION ===")
    print("Store this plaintext token securely - it will NOT be shown again.\n")
    print(f"  New shared token : {new_token}")
    print(f"  Old token valid until: {grace_until}  (grace: {gracia_horas}h)")
    print(f"\nHash written to: {tokens_file}")


def cmd_admin(tokens_file: Path, config_file: Path) -> None:
    """Generate a new admin token and update its hash in config.yaml."""
    token = _generate_token()
    token_hash = hash_token(token)

    updated_config = False
    if config_file.exists():
        lines = config_file.read_text(encoding="utf-8").splitlines()
        new_lines = []
        for line in lines:
            if line.strip().startswith("admin_token_hash:"):
                indent = len(line) - len(line.lstrip())
                new_lines.append(" " * indent + f'admin_token_hash: "{token_hash}"')
                updated_config = True
            else:
                new_lines.append(line)
        config_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print("\n=== NEW ADMIN TOKEN ===")
    print("Store this plaintext token securely - it will NOT be shown again.\n")
    print(f"  Admin token: {token}")

    if updated_config:
        print(f"\nadmin_token_hash updated in: {config_file}")
    else:
        print(f"\nWARNING: Could not update {config_file}")
        print(f'Manually set:  admin_token_hash: "{token_hash}"')


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and rotate authentication tokens for monitoreo-servidor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--shared",
        action="store_true",
        help="Generate a new shared token for all rooms.",
    )
    group.add_argument(
        "--rotar-shared",
        action="store_true",
        help="Rotate the shared token.",
    )
    group.add_argument(
        "--admin",
        action="store_true",
        help="Generate a new admin token.",
    )
    parser.add_argument(
        "--gracia-horas",
        type=int,
        default=24,
        metavar="HOURS",
        help="Grace window in hours for the old token after rotation (default: 24).",
    )
    parser.add_argument(
        "--tokens-file",
        type=Path,
        default=_DEFAULT_TOKENS_FILE,
        metavar="PATH",
        help=f"Path to tokens.yaml (default: {_DEFAULT_TOKENS_FILE}).",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=_DEFAULT_CONFIG_FILE,
        metavar="PATH",
        help=f"Path to config.yaml, used for --admin (default: {_DEFAULT_CONFIG_FILE}).",
    )

    args = parser.parse_args()

    if args.shared:
        cmd_shared(args.tokens_file)
    elif args.rotar_shared:
        cmd_rotar_shared(args.gracia_horas, args.tokens_file)
    elif args.admin:
        cmd_admin(args.tokens_file, args.config_file)


if __name__ == "__main__":
    main()
