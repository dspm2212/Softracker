"""CLI tool for generating and rotating room authentication tokens.

Generates cryptographically random tokens, stores their SHA-256 hashes
in tokens.yaml, and prints plaintext tokens once to the console.

Usage:
    python scripts/generar_tokens.py --salas SALA-01 SALA-02 SALA-03
    python scripts/generar_tokens.py --rotar SALA-01 --gracia-horas 24
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


def cmd_salas(sala_codes: list[str], tokens_file: Path) -> None:
    """Generate a fresh token for each room in sala_codes."""
    data = _load_yaml(tokens_file)
    data.setdefault("rooms", {})

    generated: list[tuple[str, str]] = []
    for code in sala_codes:
        token = _generate_token()
        data["rooms"][code] = {
            "active_hash": hash_token(token),
            "active_since": _now_utc_str(),
            "previous_hash": None,
            "previous_until": None,
        }
        generated.append((code, token))

    _save_yaml(data, tokens_file)

    print("\n=== NEW ROOM TOKENS ===")
    print("Store these plaintext tokens securely — they will NOT be shown again.\n")
    for code, token in generated:
        print(f"  {code:20s}  {token}")
    print(f"\nHashes written to: {tokens_file}")


def cmd_rotar(sala_code: str, gracia_horas: int, tokens_file: Path) -> None:
    """Rotate the token for an existing room, keeping the old one valid temporarily."""
    data = _load_yaml(tokens_file)
    rooms: dict = data.get("rooms", {})

    if sala_code not in rooms:
        print(f"ERROR: Room '{sala_code}' not found in {tokens_file}", file=sys.stderr)
        sys.exit(1)

    old_active_hash = rooms[sala_code].get("active_hash")
    new_token = _generate_token()
    grace_until = (
        datetime.now(timezone.utc) + timedelta(hours=gracia_horas)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    data["rooms"][sala_code] = {
        "active_hash": hash_token(new_token),
        "active_since": _now_utc_str(),
        "previous_hash": old_active_hash,
        "previous_until": grace_until,
    }

    _save_yaml(data, tokens_file)

    print(f"\n=== TOKEN ROTATION: {sala_code} ===")
    print("Store this plaintext token securely — it will NOT be shown again.\n")
    print(f"  New token : {new_token}")
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
    print("Store this plaintext token securely — it will NOT be shown again.\n")
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
        "--salas",
        nargs="+",
        metavar="SALA_CODE",
        help="Generate a new token for one or more rooms (e.g. --salas SALA-01 SALA-02).",
    )
    group.add_argument(
        "--rotar",
        metavar="SALA_CODE",
        help="Rotate the token for an existing room.",
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

    if args.salas:
        cmd_salas(args.salas, args.tokens_file)
    elif args.rotar:
        cmd_rotar(args.rotar, args.gracia_horas, args.tokens_file)
    elif args.admin:
        cmd_admin(args.tokens_file, args.config_file)


if __name__ == "__main__":
    main()
