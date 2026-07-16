"""Resolve Page Access Token for configured META_PAGE_ID."""

from __future__ import annotations

import argparse
from typing import Any

from meta_setup import (
    _extract_ig_account_id,
    load_dotenv_values,
    mask_secret,
    print_env_status,
    require_env_file,
    resolve_page_access_token,
    update_env_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Page Access Token via /me/accounts or direct page lookup.",
    )
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Update META_PAGE_ACCESS_TOKEN (and IG account id if found) in .env.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        require_env_file()
        values = load_dotenv_values()
        required = ("META_USER_ACCESS_TOKEN", "META_PAGE_ID")
        missing = print_env_status(values, required=required)
        if missing:
            raise SystemExit(1)

        page_id = values["META_PAGE_ID"]
        user_token = values["META_USER_ACCESS_TOKEN"]
        graph_version = values.get("META_GRAPH_API_VERSION") or "v25.0"

        print(f"\nZoek Page Access Token voor page_id={page_id} ...")
        page_payload, source = resolve_page_access_token(
            page_id=page_id,
            user_access_token=user_token,
            graph_api_version=graph_version,
        )

        page_name = page_payload.get("name", "-")
        page_token = page_payload.get("access_token")
        ig_account_id = _extract_ig_account_id(page_payload)

        print("\nPage lookup resultaat:")
        print(f"  Bron           : {source}")
        print(f"  Page ID        : {page_payload.get('id', '-')}")
        print(f"  Page naam      : {page_name}")
        print(f"  Page token     : {mask_secret(page_token) if page_token else '<niet beschikbaar>'}")
        print(
            f"  IG account ID  : {ig_account_id or values.get('META_INSTAGRAM_BUSINESS_ACCOUNT_ID') or '<niet gevonden>'}"
        )

        if not page_token:
            print("\nMeta gaf geen Page Access Token terug.")
            print("Mogelijke oorzaken:")
            print("  - user token mist pages_show_list / pages_read_engagement")
            print("  - gebruiker heeft geen admin/relevante rol op de Page")
            print("  - app staat in development mode en user is geen tester/admin")
            print("  - verkeerde META_PAGE_ID")
            print("  - user token is verlopen (run scripts/refresh_all_tokens.py)")
            raise SystemExit(1)

        if args.update_env:
            updates: dict[str, str] = {"META_PAGE_ACCESS_TOKEN": str(page_token)}
            if ig_account_id:
                updates["META_INSTAGRAM_BUSINESS_ACCOUNT_ID"] = ig_account_id
            update_env_file(updates)
            print("\n.env bijgewerkt:")
            print("  - META_PAGE_ACCESS_TOKEN")
            if ig_account_id:
                print("  - META_INSTAGRAM_BUSINESS_ACCOUNT_ID")
        else:
            print("\nTip: voeg --update-env toe om .env automatisch bij te werken.")

    except FileNotFoundError as exc:
        print(f"Fout: {exc}")
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(f"Fout: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
