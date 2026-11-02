#!/usr/bin/env python3
"""Render static SVGs from GitHub's anonymous, publicly visible calendar."""

import argparse
from datetime import date, timedelta
from html import escape
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


PALETTES = {
    "light": ("#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"),
    "dark": ("#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"),
}
TEXT_COLORS = {"light": "#57606a", "dark": "#9198a1"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.days = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "data-date" not in values:
            return
        if tag != "td" or "ContributionCalendar-day" not in values.get("class", "").split():
            raise ValueError("GitHub contribution calendar markup changed.")
        day = date.fromisoformat(values["data-date"])
        level = values.get("data-level")
        if level not in {"0", "1", "2", "3", "4"} or day in self.days:
            raise ValueError("Invalid or duplicate contribution day.")
        self.days[day] = int(level)


def fetch_calendar(user):
    url = f"https://github.com/users/{user}/contributions"
    # No authentication, cookies, token environment variables or private APIs.
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; static-contribution-calendar)",
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urlopen(request, timeout=30) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("Unexpectedly large contribution calendar response.")
    parser = CalendarParser()
    parser.feed(body.decode("utf-8"))
    days = sorted(parser.days.items())
    if not 365 <= len(days) <= 367:
        raise ValueError(f"Expected a full public contribution year; received {len(days)} days.")
    if (days[-1][0] - days[0][0]).days + 1 != len(days):
        raise ValueError("The public calendar is incomplete; existing SVGs are preserved.")
    return days


def render_svg(user, days, theme):
    first, last = days[0][0], days[-1][0]
    sunday = first - timedelta(days=(first.weekday() + 1) % 7)
    weeks = (last - sunday).days // 7 + 1
    cell, pitch, left, top = 11, 15, 28, 48
    width, height = left + weeks * pitch + 16, 188
    colors, text = PALETTES[theme], TEXT_COLORS[theme]
    label = f"{MONTHS[first.month - 1]} {first.day}, {first.year} – {MONTHS[last.month - 1]} {last.day}, {last.year}"
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="calendar-title calendar-description">',
        f'<title id="calendar-title">Public GitHub contribution activity for {escape(user)}</title>',
        f'<desc id="calendar-description">{escape(label)}. Each square is one day from the publicly visible GitHub calendar. Green intensity follows GitHub activity levels zero through four. Source: https://github.com/users/{escape(user)}/contributions. No private repository data is requested.</desc>',
        f'<g font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" fill="{text}">',
        '<text x="0" y="17" font-size="14" font-weight="600">Contribution activity</text>',
        f'<text x="{width - 4}" y="17" text-anchor="end" font-size="11">{escape(label)}</text>',
    ]
    for weekday, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        svg.append(f'<text x="0" y="{top + weekday * pitch + 9}" font-size="9">{name}</text>')
    months = {}
    for day, _ in days:
        months.setdefault((day.year, day.month), day)
    for (_, month), day in months.items():
        x = left + ((day - sunday).days // 7) * pitch
        svg.append(f'<text x="{x}" y="36" font-size="10">{MONTHS[month - 1]}</text>')
    svg.append('</g>')
    for day, level in days:
        column = (day - sunday).days // 7
        row = (day.weekday() + 1) % 7
        svg.append(
            f'<rect x="{left + column * pitch}" y="{top + row * pitch}" width="{cell}" height="{cell}" rx="2" fill="{colors[level]}" data-date="{day.isoformat()}" data-level="{level}">'
            f'<title>{day.isoformat()}: GitHub activity level {level} of 4</title></rect>'
        )
    legend_x, legend_y = width - 135, 167
    svg.append(f'<g font-family="-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" fill="{text}" font-size="10">')
    svg.append(f'<text x="{legend_x - 7}" y="{legend_y + 9}" text-anchor="end">Less</text>')
    for level, color in enumerate(colors):
        svg.append(f'<rect x="{legend_x + level * pitch}" y="{legend_y}" width="{cell}" height="{cell}" rx="2" fill="{color}"/>')
    svg.append(f'<text x="{legend_x + 5 * pitch + 3}" y="{legend_y + 9}">More</text>')
    svg.append('</g></svg>')
    result = "\n".join(svg) + "\n"
    root = ET.fromstring(result)
    cells = [element for element in root.iter() if "data-date" in element.attrib]
    if len(cells) != len(days):
        raise ValueError("Rendered calendar does not match the public data.")
    return result.encode("utf-8")


def replace_images(images):
    """Stage both validated images first, preserving old assets on failure."""
    staged, originals, replaced = {}, {}, []
    try:
        for path, contents in images.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            originals[path] = path.read_bytes() if path.exists() else None
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".contributions-", suffix=".tmp", delete=False) as output:
                staged[path] = Path(output.name)
                output.write(contents)
        for path, temporary in staged.items():
            os.replace(temporary, path)
            replaced.append(path)
    except Exception:
        for path in replaced:
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(originals[path])
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default="hungkhuu404")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", args.user):
        parser.error("Invalid GitHub username.")
    days = fetch_calendar(args.user)
    assets = Path(__file__).resolve().parents[2] / "assets"
    images = {
        assets / "contributions.svg": render_svg(args.user, days, "light"),
        assets / "contributions-dark.svg": render_svg(args.user, days, "dark"),
    }
    replace_images(images)
    print(f"Generated light/dark contribution heatmaps for {args.user}: {len(days)} public calendar days, {days[0][0]} through {days[-1][0]}.")


if __name__ == "__main__":
    main()
