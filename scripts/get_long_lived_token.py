"""Exchange short-lived Meta user token for a long-lived user token."""

from __future__ import annotations

import argparse

from meta_setup import (
    exchange_long_lived_user_token,
    load_dotenv_values,
    mask_secret,
    print_env_status,
    require_env_file,
    update_env_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert short-lived META_USER_ACCESS_TOKEN to long-lived token.",
    )
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Update META_USER_ACCESS_TOKEN in .env with the new long-lived token.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        require_env_file()
        values = load_dotenv_values()
        required = ("META_APP_ID", "META_APP_SECRET", "META_USER_ACCESS_TOKEN")
        missing = print_env_status(values, required=required)
        if missing:
            raise SystemExit(1)

        print("\nStart token exchange...")
        result = exchange_long_lived_user_token(
            app_id=values["META_APP_ID"],
            app_secret=values["META_APP_SECRET"],
            short_lived_token=values["META_USER_ACCESS_TOKEN"],
            graph_api_version=values.get("META_GRAPH_API_VERSION") or "v25.0",
        )

        long_lived_token = str(result["access_token"])
        expires_in = result.get("expires_in")
        token_type = result.get("token_type", "bearer")

        print("\nLong-lived token aangemaakt.")
        print(f"  Token (masked): {mask_secret(long_lived_token)}")
        print(f"  Token type    : {token_type}")
        print(f"  Expires in    : {expires_in if expires_in is not None else 'onbekend'} seconden")

        if args.update_env:
            update_env_file({"META_USER_ACCESS_TOKEN": long_lived_token})
            print("\n.env bijgewerkt: META_USER_ACCESS_TOKEN")
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
