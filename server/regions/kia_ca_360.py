"""Kia Canada's 360-degree exterior turntables (the "Exterior 360" in kia.ca's Build & Price).

kia.ca's Build & Price page carries its whole lineup as JSON (a `vehicle-list` attribute): for every
model, year and paint code, `exterior360ImageUrls` lists 36 transparent frames, 10 degrees apart,
frame 01 being the front three-quarter view of the driver's side. The page is big (about 30 MB),
so it's read at most once a month and boiled down to a small index kept in the data folder.

The paint codes are the same ones Kia Connect reports (`exteriorColor`, e.g. "HRB" Heritage Blue).
kia.ca only lists current model years, so an older car gets the nearest year of the same model in
the same paint (the body rarely changes between neighbouring years).
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("server.kia_ca_360")

SITE = "https://www.kia.ca"
CATALOG_URL = SITE + "/en/shopping-tools/build-and-price"
INDEX_FILE = "kia-ca-360.json"
MAX_AGE = 30 * 24 * 3600


def build_index(page: str) -> list[dict[str, Any]]:
    """[{"model", "year", "paint", "frames": [absolute .webp URLs]}] from the Build & Price HTML."""
    start = page.find('vehicle-list="')
    if start < 0:
        raise ValueError("kia.ca's Build & Price page has no vehicle list any more")
    start += len('vehicle-list="')
    data = json.loads(html.unescape(page[start:page.find('"', start)]))
    rows: dict[tuple[str, str, str], list[str]] = {}

    def walk(node: Any, model: str, year: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("model"), str):
                model = node["model"]
            elif isinstance(node.get("name"), str) and not model:
                model = node["name"]
            if isinstance(node.get("year"), str):
                year = node["year"]
            urls = node.get("exterior360ImageUrls")
            if isinstance(urls, list) and isinstance(node.get("colourCode"), str):
                frames = [SITE + u for u in urls if isinstance(u, str) and u.endswith(".webp")]
                if len(frames) >= 8 and model and year:
                    rows.setdefault((model, year, node["colourCode"]), frames)
            for value in node.values():
                walk(value, model, year)
        elif isinstance(node, list):
            for value in node:
                walk(value, model, year)

    walk(data, "", "")
    return [{"model": m, "year": y, "paint": p, "frames": f} for (m, y, p), f in sorted(rows.items())]


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def match(index: list[dict[str, Any]], model: str, year: int | None, paint: str) -> list[str] | None:
    """The best turntable for a car: same paint, then the same model name (exact beats same first
    word: "Sportage" for a "SPORTAGE HYBRID"), then the nearest year."""
    want = _words(model)
    if not want or not paint:
        return None
    best: tuple[tuple[int, int], list[str]] | None = None
    for row in index:
        if row.get("paint") != paint:
            continue
        have = _words(row.get("model", ""))
        if have == want:
            name_score = 2
        elif have and have[0] == want[0]:
            name_score = 1
        else:
            continue
        try:
            distance = abs(int(row["year"]) - int(year)) if year else 0
        except (TypeError, ValueError):
            distance = 99
        score = (name_score, -distance)
        if best is None or score > best[0]:
            best = (score, row["frames"])
    return best[1] if best else None


class Catalog:
    """The index, from the data folder when it's fresh, otherwise rebuilt from kia.ca."""

    def __init__(self, cache_dir: Path | None, fetch: Callable[[str], str] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.cache_dir = cache_dir
        self.fetch = fetch or _fetch_page
        self.clock = clock
        self._index: list[dict[str, Any]] | None = None
        self._loaded_at = 0.0

    def index(self) -> list[dict[str, Any]]:
        now = self.clock()
        if self._index is not None and now - self._loaded_at < MAX_AGE:
            return self._index
        path = self.cache_dir / INDEX_FILE if self.cache_dir else None
        if path and path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if now - float(saved["fetched_at"]) < MAX_AGE:
                    self._index, self._loaded_at = saved["rows"], float(saved["fetched_at"])
                    return self._index
            except (OSError, ValueError, KeyError, TypeError):
                pass
        rows = build_index(self.fetch(CATALOG_URL))
        log.info("Read kia.ca's 360 views: %d model, year and paint combinations.", len(rows))
        self._index, self._loaded_at = rows, now
        if path:
            path.write_text(json.dumps({"fetched_at": now, "rows": rows}), encoding="utf-8")
        return rows


def _fetch_page(url: str) -> str:
    import requests
    res = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    return res.text
