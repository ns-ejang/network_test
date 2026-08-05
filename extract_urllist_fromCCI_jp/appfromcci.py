import argparse
import csv
import os
import sys
from pathlib import Path

import requests

ENV_FILE = Path(__file__).resolve().parent / ".env"
APP_LIST_PATH = "/api/v2/services/cci/app"
DOMAIN_PATH = "/api/v2/services/cci/domain"


def load_env_file(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_tenant(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return value
    if not value.startswith("http"):
        value = f"https://{value}"
    host = value.split("://", 1)[-1]
    if "." not in host:
        value = f"{value}.goskope.com"
    return value.rstrip("/")


load_env_file(ENV_FILE)

TENANT = normalize_tenant(os.environ.get("NETSKOPE_TENANT", ""))
TOKEN = os.environ.get("NETSKOPE_API_TOKEN", "")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "genai_urls.csv")
CATEGORY = os.environ.get("CATEGORY", "Generative AI")
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "500"))

REQUIRED_ENV = {
    "NETSKOPE_TENANT": TENANT,
    "NETSKOPE_API_TOKEN": TOKEN,
}


def validate_config():
    missing = [name for name, value in REQUIRED_ENV.items() if not value]
    if missing:
        print(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in your values.",
            file=sys.stderr,
        )
        sys.exit(1)


def api_headers():
    return {
        "Netskope-Api-Token": TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def api_url(path: str) -> str:
    return f"{TENANT}{path}"


def request_api(method: str, path: str, **kwargs):
    resp = requests.request(
        method,
        api_url(path),
        headers=api_headers(),
        timeout=60,
        **kwargs,
    )
    return resp


def format_api_error(resp: requests.Response, context: str) -> str:
    body = resp.text.strip()
    if len(body) > 500:
        body = body[:500] + "..."
    lines = [
        f"{context} failed: HTTP {resp.status_code}",
        f"URL: {resp.url}",
    ]
    if body:
        lines.append(f"Response: {body}")
    if resp.status_code == 404:
        lines.extend(
            [
                "",
                "404 troubleshooting:",
                "1. Confirm NETSKOPE_TENANT matches the URL in your admin console",
                "   (Settings > General, e.g. https://<tenant>.goskope.com).",
                "2. Open Settings > Tools > REST API v2 > API Documentation",
                "   and verify /api/v2/services/cci/app exists for your tenant.",
                "3. Ensure the API token role includes CCI read/manage permissions.",
                "4. Run: python3 appfromcci.py --check",
            ]
        )
    elif resp.status_code in (401, 403):
        lines.extend(
            [
                "",
                "Auth troubleshooting:",
                "1. Regenerate the REST API v2 token.",
                "2. Assign a role with CCI permissions to the service account.",
            ]
        )
    return "\n".join(lines)


def raise_for_api_error(resp: requests.Response, context: str):
    if resp.ok:
        return
    print(format_api_error(resp, context), file=sys.stderr)
    resp.raise_for_status()


def fetch_apps_page(offset: int):
    payload = {"category": CATEGORY, "limit": PAGE_SIZE, "offset": offset}
    params = payload.copy()

    post_resp = request_api("POST", APP_LIST_PATH, json=payload)
    if post_resp.status_code == 404:
        get_resp = request_api("GET", APP_LIST_PATH, params=params)
        raise_for_api_error(get_resp, "CCI app lookup (GET)")
        return get_resp.json()

    raise_for_api_error(post_resp, "CCI app lookup (POST)")
    return post_resp.json()


def fetch_all_apps():
    all_apps = []
    offset = 0
    total_query_count = None

    while True:
        body = fetch_apps_page(offset)
        data = body.get("data", [])

        if total_query_count is None:
            total_query_count = body.get("total_query_count")
            if total_query_count is not None:
                print(f"Total apps in '{CATEGORY}': {total_query_count}")

        if not data:
            break

        all_apps.extend(data)
        offset += PAGE_SIZE

        if total_query_count is not None and offset >= total_query_count:
            break
        if len(data) < PAGE_SIZE:
            break

    print(f"Fetched {len(all_apps)} apps")
    if total_query_count is not None and len(all_apps) != total_query_count:
        print(
            f"Warning: expected {total_query_count} apps but got {len(all_apps)}",
            file=sys.stderr,
        )
    return all_apps


def fetch_domains(app_ids):
    rows = []
    batch_size = 50

    for i in range(0, len(app_ids), batch_size):
        batch = ";".join(app_ids[i : i + batch_size])
        resp = request_api("GET", DOMAIN_PATH, params={"ids": batch})
        raise_for_api_error(resp, "CCI domain lookup")
        for app in resp.json().get("data", []):
            for domain in app.get("discovery_domains", []):
                rows.append((app["app_name"], domain))
    return rows


def run_check():
    validate_config()
    print(f"Tenant: {TENANT}")
    print(f"Category: {CATEGORY}")
    print()

    probes = [
        ("GET", "/api/v2/services/cci/tags/all", {}),
        ("GET", APP_LIST_PATH, {"category": CATEGORY, "limit": 1, "offset": 0}),
        ("POST", APP_LIST_PATH, {"category": CATEGORY, "limit": 1, "offset": 0}),
    ]

    for method, path, payload in probes:
        if method == "GET":
            resp = request_api(method, path, params=payload)
        else:
            resp = request_api(method, path, json=payload)

        status = "OK" if resp.ok else "FAIL"
        print(f"[{status}] {method} {path} -> HTTP {resp.status_code}")
        if not resp.ok:
            snippet = resp.text.strip().replace("\n", " ")
            if len(snippet) > 120:
                snippet = snippet[:120] + "..."
            if snippet:
                print(f"       {snippet}")
    print()
    print("If all probes fail with 404, the tenant URL is likely wrong or CCI API is unavailable.")


def main():
    parser = argparse.ArgumentParser(description="Export CCI Generative AI app domains to CSV")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Test tenant URL, token, and CCI API connectivity",
    )
    args = parser.parse_args()

    validate_config()

    if args.check:
        run_check()
        return

    all_apps = fetch_all_apps()
    app_ids = [str(app["id"]) for app in all_apps]
    rows = fetch_domains(app_ids)

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["app_name", "domain"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} domains to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
