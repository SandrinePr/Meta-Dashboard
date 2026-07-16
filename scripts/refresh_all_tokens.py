"""Refresh long-lived user token and page access token in `.env`."""

from __future__ import annotations

from meta_setup import (
    load_dotenv_values,
    mask_secret,
    print_env_status,
    refresh_meta_tokens,
    require_env_file,
    update_env_file,
)

TOKEN_REFRESH_INSTRUCTION = (
    "Als een token verlopen is:\n"
    "  1. Genereer een nieuwe User Token in Graph API Explorer\n"
    "  2. Zet die tijdelijk in META_USER_ACCESS_TOKEN in .env\n"
    "  3. Run: python scripts/refresh_all_tokens.py\n"
    "  4. Run: python sync.py --all"
)


def main() -> None:
    try:
        require_env_file()
        values = load_dotenv_values()
        required = (
            "META_APP_ID",
            "META_APP_SECRET",
            "META_USER_ACCESS_TOKEN",
            "META_PAGE_ID",
        )
        missing = print_env_status(values, required=required)
        if missing:
            print(f"\n{TOKEN_REFRESH_INSTRUCTION}")
            raise SystemExit(1)

        print("\nStart volledige token refresh...")
        updates = refresh_meta_tokens(values)
        update_env_file(updates)

        print("\nTokens vernieuwd en .env bijgewerkt:")
        print(f"  META_USER_ACCESS_TOKEN : {mask_secret(updates['META_USER_ACCESS_TOKEN'])}")
        print(f"  META_PAGE_ACCESS_TOKEN : {mask_secret(updates['META_PAGE_ACCESS_TOKEN'])}")
        if "META_INSTAGRAM_BUSINESS_ACCOUNT_ID" in updates:
            print(f"  META_INSTAGRAM_BUSINESS_ACCOUNT_ID : {updates['META_INSTAGRAM_BUSINESS_ACCOUNT_ID']}")
        print("\nVolgende stap: python sync.py --all")

    except FileNotFoundError as exc:
        print(f"Fout: {exc}")
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(f"Fout: {exc}")
        print(f"\n{TOKEN_REFRESH_INSTRUCTION}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
