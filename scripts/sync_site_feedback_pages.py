"""Synchronize versioned public PHP manifest into Mongo. Dry-run unless --apply.

Usage: python -m scripts.sync_site_feedback_pages [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPHandler, HTTPSHandler, HTTPRedirectHandler

from domain.site_feedback import PAGE_KEY_PATTERN

ROUTE = re.compile(r"^/(?:[a-z0-9-]+/|\{slug\}/)*$")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("Manifest URL must not redirect")


def validate_manifest(payload: dict) -> list[dict]:
    if payload.get("version") != 1 or not isinstance(payload.get("pages"), list):
        raise ValueError("Unsupported site page manifest")
    result, seen = [], set()
    if not 1 <= len(payload["pages"]) <= 200:
        raise ValueError("Unexpected number of pages")
    for row in payload["pages"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid page row")
        key, title, route, access, order = (row.get(k) for k in
            ("key", "title", "route", "access", "sort_order"))
        if (not isinstance(key, str) or not PAGE_KEY_PATTERN.fullmatch(key)
                or key in seen or not isinstance(title, str) or not 1 <= len(title) <= 100
                or not isinstance(route, str) or not ROUTE.fullmatch(route)
                or route.count("{slug}") > 1 or access not in {"public", "admin_only"}
                or type(order) is not int or not 0 <= order <= 10000):
            raise ValueError("Invalid page route metadata")
        seen.add(key)
        result.append({"_id": key, "title": title, "route": route, "access": access, "sort_order": order})
    return result


def fetch_manifest(base_url: str) -> list[dict]:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in
        {"127.0.0.1", "localhost", "::1"})) or not parsed.hostname or parsed.username:
        raise ValueError("Site URL must be HTTPS (or local loopback HTTP)")
    # URL is configured by bot owner, not derived from Telegram input.
    url = base_url.rstrip("/") + "/api/site-feedback/pages/"
    opener = build_opener(HTTPHandler(), HTTPSHandler(), NoRedirect())
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Lackey-SitePagesSync/1"})
    with opener.open(request, timeout=6) as response:
        if response.url != url:
            raise ValueError("Unexpected manifest redirect")
        raw = response.read(131073)
    if len(raw) > 131072:
        raise ValueError("Manifest too large")
    return validate_manifest(json.loads(raw))


async def run(apply: bool) -> None:
    from config import settings
    from motor.motor_asyncio import AsyncIOMotorClient

    rows = fetch_manifest(settings.SITE_FEEDBACK_BASE_URL)
    print(f"Manifest: {len(rows)} pages (mode={'APPLY' if apply else 'DRY RUN'})")
    for row in rows:
        print(f"  {row['_id']:<24} {row['route']}")
    if not apply:
        print("No database changes. Re-run with --apply after review.")
        return
    client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000)
    try:
        database = client[settings.DB_NAME]
        for row in rows:
            key = row.pop("_id")
            await database.site_feedback_pages.update_one({"_id": key}, {
                "$set": row,
                "$setOnInsert": {"visible": True, "created_at": datetime.now(timezone.utc)},
            }, upsert=True)
        print("Applied. Existing visibility, reviews, and unknown pages preserved.")
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply changes after inspecting dry-run")
    asyncio.run(run(parser.parse_args().apply))
