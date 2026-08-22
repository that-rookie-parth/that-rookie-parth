#!/usr/bin/env python3
"""Refresh the profile's contribution-activity and language-mix SVG cards."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_REST_URL = "https://api.github.com"
API_VERSION = "2026-03-10"
DEFAULT_USERNAME = "parthkulshreshtha"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "assets"

# GitHub Linguist already excludes files marked generated or vendored. These
# additional categories are intentionally outside this source-code-only card.
EXCLUDED_LANGUAGES = {
    "CSS",
    "Dockerfile",
    "HTML",
    "Jupyter Notebook",
    "Less",
    "Markdown",
    "MDX",
    "Sass",
    "SCSS",
    "TeX",
}

LANGUAGE_COLORS = {
    "Python": ("#3776AB", "#3776AB"),
    "TypeScript": ("#4CC3E0", "#004C67"),
    "PowerShell": ("#B7472A", "#B7472A"),
    "JavaScript": ("#F1E05A", "#B08900"),
    "C++": ("#F34B7D", "#A3133F"),
    "C": ("#A8B9CC", "#4A5563"),
    "Shell": ("#89E051", "#39711F"),
    "Java": ("#E76F00", "#9A3B00"),
    "Go": ("#00ADD8", "#00677F"),
    "Rust": ("#DEA584", "#8B4A2B"),
}
FALLBACK_COLORS = [
    ("#63C69A", "#247A55"),
    ("#B79BF0", "#6742C0"),
    ("#F59E0B", "#9A5800"),
    ("#38BDF8", "#036A91"),
]

THEMES = {
    "dark": {
        "root": "#EDE8F7",
        "card": "#0D0A14",
        "stroke": "#2A2140",
        "muted": "#A99FC0",
        "line": "#8B6BE1",
        "latest": "#63C69A",
    },
    "light": {
        "root": "#1A1526",
        "card": "#FFFFFF",
        "stroke": "#DED5F2",
        "muted": "#5F5677",
        "line": "#6742C0",
        "latest": "#247A55",
    },
}

GRAPHQL_QUERY = """
query ProfileContributionCalendar($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            contributionCount
            date
          }
        }
      }
    }
  }
}
"""


class GeneratorError(RuntimeError):
    """A clear, user-facing generator failure."""


@dataclass(frozen=True)
class LanguageShare:
    name: str
    bytes: int
    percent: float


def previous_year(day: date) -> date:
    """Return the same calendar date one year earlier, clamping leap day."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def month_keys(start: date, end: date) -> list[tuple[int, int]]:
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return keys


def contribution_series(
    days: Iterable[dict[str, Any]], start: date, end: date
) -> tuple[int, list[tuple[tuple[int, int], int]]]:
    totals: dict[tuple[int, int], int] = defaultdict(int)
    total = 0
    for item in days:
        item_date = date.fromisoformat(item["date"])
        if start <= item_date <= end:
            count = int(item["contributionCount"])
            totals[(item_date.year, item_date.month)] += count
            total += count
    return total, [(key, totals[key]) for key in month_keys(start, end)]


def language_shares(language_bytes: dict[str, int], limit: int = 4) -> list[LanguageShare]:
    included = {
        name: int(size)
        for name, size in language_bytes.items()
        if name not in EXCLUDED_LANGUAGES and int(size) > 0
    }
    total = sum(included.values())
    if not total:
        raise GeneratorError("No included source-language bytes were returned")
    ordered = sorted(included.items(), key=lambda item: (-item[1], item[0]))
    if len(ordered) > limit:
        visible = ordered[: limit - 1]
        visible.append(("Other", sum(size for _, size in ordered[limit - 1 :])))
        ordered = visible
    return [
        LanguageShare(name, size, round(size * 100 / total, 1))
        for name, size in ordered
    ]


def request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, str]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "profile-stats-generator",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            return json.load(response), response_headers
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise GeneratorError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GeneratorError(f"GitHub API request failed: {exc.reason}") from exc


def fetch_contribution_days(
    username: str, token: str, start: date, end: date
) -> list[dict[str, Any]]:
    payload = {
        "query": GRAPHQL_QUERY,
        "variables": {
            "login": username,
            "from": f"{start.isoformat()}T00:00:00Z",
            "to": f"{end.isoformat()}T23:59:59Z",
        },
    }
    data, _ = request_json(GITHUB_GRAPHQL_URL, token, method="POST", payload=payload)
    if data.get("errors"):
        raise GeneratorError(f"GitHub GraphQL error: {data['errors']}")
    try:
        weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    except (KeyError, TypeError) as exc:
        raise GeneratorError("GitHub GraphQL response did not contain a contribution calendar") from exc
    return [day for week in weeks for day in week["contributionDays"]]


def fetch_public_language_bytes(username: str, token: str) -> dict[str, int]:
    query = urllib.parse.urlencode({"type": "owner", "per_page": 100, "page": 1})
    url: str | None = f"{GITHUB_REST_URL}/users/{urllib.parse.quote(username)}/repos?{query}"
    repositories: list[dict[str, Any]] = []
    while url:
        page, headers = request_json(url, token)
        if not isinstance(page, list):
            raise GeneratorError("GitHub repository response was not a list")
        repositories.extend(page)
        url = next_link(headers.get("link", ""))

    totals: dict[str, int] = defaultdict(int)
    for repository in repositories:
        if repository.get("private") or repository.get("fork"):
            continue
        if repository.get("name", "").casefold() == username.casefold():
            continue
        languages, _ = request_json(repository["languages_url"], token)
        if not isinstance(languages, dict):
            raise GeneratorError(f"Language response for {repository['name']} was not an object")
        for name, size in languages.items():
            totals[name] += int(size)
    return dict(totals)


def next_link(header: str) -> str | None:
    for part in header.split(","):
        section = part.strip().split(";")
        if len(section) == 2 and section[1].strip() == 'rel="next"':
            return section[0].strip()[1:-1]
    return None


def activity_svg(
    theme_name: str,
    total: int,
    series: list[tuple[tuple[int, int], int]],
    start: date,
    end: date,
) -> str:
    theme = THEMES[theme_name]
    if len(series) != 13:
        raise GeneratorError(f"Expected 13 monthly points, received {len(series)}")
    chart_x = [31, 97, 163, 229, 295, 361, 427, 493, 559, 625, 691, 757, 829]
    axis_max = max(max(value for _, value in series), 1)
    points = [
        (x, round(176 - (value / axis_max) * 112))
        for x, (_, value) in zip(chart_x, series)
    ]
    path = " ".join(
        ("M" if index == 0 else "L") + f"{x} {y}"
        for index, (x, y) in enumerate(points)
    )
    label_indexes = range(0, 13, 2)
    labels = []
    for index in label_indexes:
        year, month = series[index][0]
        anchor = ' text-anchor="end"' if index == 12 else ""
        labels.append(
            f'  <text x="{chart_x[index]}" y="200"{anchor} class="mono" '
            f'font-size="10" fill="{theme["muted"]}">{calendar.month_abbr[month]}</text>'
        )
    latest_x, latest_y = points[-1]
    description = (
        f"A line chart shows {total} profile contributions between "
        f"{calendar.month_name[start.month]} {start.year} and "
        f"{calendar.month_name[end.month]} {end.year}."
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="220" viewBox="0 0 860 220" role="img" aria-labelledby="title description" fill="{theme['root']}">
  <title id="title">Contribution activity</title>
  <desc id="description">{escape(description)}</desc>
  <style>.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}</style>
  <rect x="1" y="1" width="858" height="218" rx="12" fill="{theme['card']}" stroke="{theme['stroke']}" stroke-width="2"/>
  <text x="24" y="38" class="mono" font-size="12" fill="{theme['muted']}">{total} contributions · last 12 months</text>
  <line x1="31" y1="64" x2="829" y2="64" stroke="{theme['stroke']}"/>
  <line x1="31" y1="120" x2="829" y2="120" stroke="{theme['stroke']}"/>
  <line x1="31" y1="176" x2="829" y2="176" stroke="{theme['stroke']}"/>
  <text x="13" y="67" class="mono" font-size="10" fill="{theme['muted']}">{axis_max}</text>
  <text x="13" y="123" class="mono" font-size="10" fill="{theme['muted']}">{axis_max // 2}</text>
  <text x="19" y="179" class="mono" font-size="10" fill="{theme['muted']}">0</text>
  <path d="{path}" fill="none" stroke="{theme['line']}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{latest_x}" cy="{latest_y}" r="5" fill="{theme['latest']}"/>
{chr(10).join(labels)}
</svg>
'''


def allocate_bar_widths(
    shares: list[LanguageShare], available: int | None = None
) -> list[int]:
    if available is None:
        available = 812 - 2 * (len(shares) - 1)
    raw = [share.bytes * available / sum(item.bytes for item in shares) for share in shares]
    widths = [int(value) for value in raw]
    remainder = available - sum(widths)
    order = sorted(range(len(raw)), key=lambda index: raw[index] - widths[index], reverse=True)
    for index in order[:remainder]:
        widths[index] += 1
    return widths


def language_svg(theme_name: str, shares: list[LanguageShare]) -> str:
    if not 1 <= len(shares) <= 4:
        raise GeneratorError(f"Expected between one and four languages, received {len(shares)}")
    theme_index = 0 if theme_name == "dark" else 1
    theme = THEMES[theme_name]
    widths = allocate_bar_widths(shares)
    rects = []
    x = 24
    for index, (share, width) in enumerate(zip(shares, widths)):
        color = LANGUAGE_COLORS.get(share.name, FALLBACK_COLORS[index])[theme_index]
        rects.append(f'    <rect x="{x}" y="64" width="{width}" height="18" fill="{color}"/>')
        x += width + 2

    legend_positions = [(29, 117), (305, 117), (581, 117), (29, 152)]
    legend = []
    for index, share in enumerate(shares):
        circle_x, baseline = legend_positions[index]
        color = LANGUAGE_COLORS.get(share.name, FALLBACK_COLORS[index])[theme_index]
        label_x = circle_x + 14
        percent_x = circle_x + 214
        legend.append(
            f'  <circle cx="{circle_x}" cy="{baseline - 4}" r="5" fill="{color}"/>'
            f'<text x="{label_x}" y="{baseline}" class="mono" font-size="12">{escape(share.name)}</text>'
            f'<text x="{percent_x}" y="{baseline}" class="mono" font-size="12" fill="{theme["muted"]}">{share.percent:.1f}%</text>'
        )

    leader = shares[0]
    description = (
        f"{leader.name} leads the source language mix after notebooks, markup, "
        "styles, configuration and generated files are excluded."
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="860" height="172" viewBox="0 0 860 172" role="img" aria-labelledby="title description" fill="{theme['root']}">
  <title id="title">Language mix</title>
  <desc id="description">{escape(description)}</desc>
  <style>.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}</style>
  <rect x="1" y="1" width="858" height="170" rx="12" fill="{theme['card']}" stroke="{theme['stroke']}" stroke-width="2"/>
  <text x="24" y="38" class="mono" font-size="12" fill="{theme['muted']}">{escape(leader.name)} leads at {leader.percent:.1f}%</text>
  <defs><clipPath id="bar"><rect x="24" y="64" width="812" height="18" rx="9"/></clipPath></defs>
  <g clip-path="url(#bar)">
{chr(10).join(rects)}
  </g>
{chr(10).join(legend)}
</svg>
'''


def write_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="UTC end date in YYYY-MM-DD form (useful for deterministic tests)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GeneratorError("GITHUB_TOKEN is required")

    start = previous_year(args.as_of)
    days = fetch_contribution_days(args.username, token, start, args.as_of)
    total, series = contribution_series(days, start, args.as_of)
    shares = language_shares(fetch_public_language_bytes(args.username, token))

    changed = []
    for theme in THEMES:
        activity_path = args.output_dir / f"activity-{theme}.svg"
        language_path = args.output_dir / f"languages-{theme}.svg"
        if write_if_changed(activity_path, activity_svg(theme, total, series, start, args.as_of)):
            changed.append(activity_path.name)
        if write_if_changed(language_path, language_svg(theme, shares)):
            changed.append(language_path.name)

    print(f"Contribution total: {total}")
    print("Languages: " + ", ".join(f"{item.name} {item.percent:.1f}%" for item in shares))
    print("Changed: " + (", ".join(changed) if changed else "none"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
