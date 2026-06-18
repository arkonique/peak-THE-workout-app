"""Collect exercise links and profile data from the PlainExercise pages.

The script reuses the fetch helper from plainexercise.py, walks the listing
pages until the site returns the no-results sentinel, then fetches each
exercise page and extracts the profile table into normalized fields.
"""

from __future__ import annotations

import json
import csv
from dataclasses import dataclass
from html.parser import HTMLParser
import sys
from pathlib import Path
from urllib.parse import urljoin

from tqdm import tqdm


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from plainexercise import get_page_content


BASE_URL = "https://plainexercise.com"
LISTING_URL_TEMPLATE = "https://plainexercise.com/exercises/?page={page}/"
NO_RESULTS_SENTINEL = "No exercises match the current filters."


@dataclass
class ExerciseLink:
    name: str
    url: str


def normalize_field_name(label: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in label.strip())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


class ExerciseLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[ExerciseLink] = []
        self._capture_depth = 0
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            if self._capture_depth:
                self._capture_depth += 1
            return

        attributes = dict(attrs)
        href = attributes.get("href")
        if href and href.startswith("/exercise/"):
            self._capture_depth = 1
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._capture_depth and self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture_depth:
            return

        self._capture_depth -= 1
        if tag == "a" and self._capture_depth == 0 and self._current_href:
            name = " ".join("".join(self._current_text).split())
            if name:
                self.items.append(
                    ExerciseLink(
                        name=name,
                        url=urljoin(BASE_URL, self._current_href),
                    )
                )
            self._current_href = None
            self._current_text = []


class ExerciseProfileParser(HTMLParser):
    def __init__(self, exercise_name: str) -> None:
        super().__init__()
        self.exercise_name = exercise_name
        self.fields: dict[str, str] = {}
        self._table_depth = 0
        self._capture_table = False
        self._capture_caption = False
        self._current_caption: list[str] = []
        self._current_row_header: list[str] = []
        self._current_row_value: list[str] = []
        self._current_row_header_text = ""
        self._current_row_value_text = ""
        self._in_row = False
        self._in_header_cell = False
        self._in_value_cell = False
        self._is_profile_table = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        class_attr = attributes.get("class", "") or ""
        classes = set(class_attr.split())

        if tag == "table":
            self._table_depth += 1
            if "w-full" in classes and "text-sm" in classes and self._table_depth == 1:
                self._capture_table = True
            return

        if not self._capture_table:
            return

        if tag == "caption":
            self._capture_caption = True
        elif tag == "tr":
            self._in_row = True
            self._current_row_header = []
            self._current_row_value = []
            self._current_row_header_text = ""
            self._current_row_value_text = ""
        elif tag == "th" and self._in_row:
            self._in_header_cell = True
        elif tag == "td" and self._in_row:
            self._in_value_cell = True

    def handle_data(self, data: str) -> None:
        if self._capture_caption:
            self._current_caption.append(data)
        elif self._in_header_cell:
            self._current_row_header.append(data)
        elif self._in_value_cell:
            self._current_row_value.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._table_depth > 0:
                self._table_depth -= 1
            if self._table_depth == 0:
                self._capture_table = False
                self._is_profile_table = False
            return

        if not self._capture_table:
            return

        if tag == "caption":
            self._capture_caption = False
            caption = " ".join("".join(self._current_caption).split())
            expected = f"Profile attributes for {self.exercise_name}"
            self._is_profile_table = caption == expected or caption.startswith("Profile attributes for ")
        elif tag == "th" and self._in_header_cell:
            self._in_header_cell = False
            self._current_row_header_text = " ".join("".join(self._current_row_header).split())
        elif tag == "td" and self._in_value_cell:
            self._in_value_cell = False
            self._current_row_value_text = " ".join("".join(self._current_row_value).split())
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._is_profile_table and self._current_row_header_text and self._current_row_value_text:
                field_name = normalize_field_name(self._current_row_header_text)
                if field_name:
                    self.fields[field_name] = self._current_row_value_text


def collect_exercise_links() -> list[dict[str, str]]:
    all_items: list[ExerciseLink] = []
    seen_urls: set[str] = set()

    page = 1
    progress = tqdm(desc="Discovering exercise pages", unit="page", dynamic_ncols=True)
    try:
        while True:
            listing_url = LISTING_URL_TEMPLATE.format(page=page)
            status, final_url, content_type, content = get_page_content(listing_url)

            progress.update(1)
            progress.set_postfix_str(f"page={page}")

            if NO_RESULTS_SENTINEL in content:
                break

            if status != 200 or "text/html" not in content_type.lower():
                raise RuntimeError(
                    f"Unexpected response for page {page}: status={status}, final_url={final_url}, content_type={content_type}"
                )

            parser = ExerciseLinkParser()
            parser.feed(content)

            if not parser.items:
                break

            for item in parser.items:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                all_items.append(item)

            page += 1
    finally:
        progress.close()

    return [{"name": item.name, "url": item.url} for item in all_items]


def enrich_exercise_record(record: dict[str, str]) -> dict[str, str]:
    status, final_url, content_type, content = get_page_content(record["url"])

    if status != 200 or "text/html" not in content_type.lower():
        raise RuntimeError(
            f"Unexpected response for exercise page {record['url']}: status={status}, final_url={final_url}, content_type={content_type}"
        )

    parser = ExerciseProfileParser(record["name"])
    parser.feed(content)

    enriched = dict(record)
    enriched.update(parser.fields)
    return enriched


def collect_exercise_details() -> list[dict[str, str]]:
    records = collect_exercise_links()
    enriched_items: list[dict[str, str]] = []

    for record in tqdm(records, desc="Fetching exercise details", unit="exercise", dynamic_ncols=True):
        enriched_items.append(enrich_exercise_record(record))

    return enriched_items


def export_items(items: list[dict[str, str]], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.suffix.lower() == ".csv":
        field_names = sorted({key for item in items for key in item.keys()})
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names)
            writer.writeheader()
            writer.writerows(items)
    else:
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    return path


def run(output_path: str | Path | None = None) -> str:
    """Return the complete exercise list with profile fields as JSON.

    If output_path is provided, the result is also saved locally. Use a .csv
    suffix to save CSV; any other suffix saves JSON.
    """

    items = collect_exercise_details()
    output = json.dumps(items, ensure_ascii=False, indent=2)

    if output_path is not None:
        export_items(items, output_path)

    return output


def main() -> None:
    print(run("all_exercises.csv"))


if __name__ == "__main__":
    main()