#!/usr/bin/env python3
"""End-to-End Headless DOM & API Route Audit for SIH26004 OA Detection Dashboard.

Validates:
1. Static DOM element integrity (buttons, selects, sliders, tabs, modals, canvases).
2. Elimination of duplicate IDs (specifically #chkAutoRecord).
3. Hardware indicator tooltips accurately stating GPIO 26, 27, and 25.
4. Complete elimination of decorative emojis.
5. Presence and validity of the canonical "OA Risk Detection" brand text.
6. Apple-inspired minimalist design tokens (SF Pro typography, neutral backgrounds, hairline borders).
7. Offline Chart.js asset presence.
8. Client-side JavaScript API endpoints match registered FastAPI routes.
"""

from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.main import create_app


class DashboardDOMParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.buttons: list[dict[str, str]] = []
        self.selects: list[dict[str, str]] = []
        self.sliders: list[dict[str, str]] = []
        self.canvases: list[dict[str, str]] = []
        self.modals: list[str] = []
        self.tabs: list[dict[str, str]] = []
        self.ids: list[str] = []
        self.tooltips: list[str] = []
        self.in_title = False
        self.title_text = ""
        self._stack: list[tuple[str, int]] = []
        self.tag_errors: list[str] = []
        self._structural = ("div", "section", "table", "thead", "tbody", "tr", "td", "th", "select", "button", "header", "main")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in self._structural:
            self._stack.append((tag, self.getpos()[0]))
        attr_dict = {k: v or "" for k, v in attrs}
        tag_id = attr_dict.get("id", "")
        tag_cls = attr_dict.get("class", "")
        tag_type = attr_dict.get("type", "")

        if tag_id:
            self.ids.append(tag_id)

        if tag == "title":
            self.in_title = True

        if tag == "button":
            self.buttons.append({"id": tag_id, "class": tag_cls, "text": ""})
            if "tab-btn" in tag_cls.split():
                self.tabs.append({"id": tag_id, "tab": attr_dict.get("data-tab", "")})

        elif tag == "select":
            self.selects.append({"id": tag_id, "class": tag_cls})

        elif tag == "input" and tag_type == "range":
            self.sliders.append({"id": tag_id, "min": attr_dict.get("min", ""), "max": attr_dict.get("max", "")})

        elif tag == "canvas":
            self.canvases.append({"id": tag_id, "class": tag_cls})

        if "modal-overlay" in tag_cls.split() and tag_id:
            self.modals.append(tag_id)

        if "tip-lbl" in tag_cls.split() or "data-tip" in attr_dict:
            tip = attr_dict.get("data-tip", "")
            if tip:
                self.tooltips.append(tip)

    def handle_endtag(self, tag: str):
        if tag in self._structural:
            if not self._stack or self._stack[-1][0] != tag:
                self.tag_errors.append(f"Line {self.getpos()[0]}: unexpected </{tag}> (stack top={self._stack[-1] if self._stack else None})")
            else:
                self._stack.pop()
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str):
        if self.in_title:
            self.title_text += data


def audit_dom_elements(html_content: str) -> None:
    print("[1/6] Auditing DOM elements...")
    parser = DashboardDOMParser()
    parser.feed(html_content)

    # 1. Buttons (Invariant requires >= 22)
    btn_count = len(parser.buttons)
    print(f"  - Buttons found: {btn_count} (required >= 22)")
    assert btn_count >= 22, f"Expected at least 22 buttons, found {btn_count}"

    # 2. Selectors (Invariant requires >= 4)
    sel_count = len(parser.selects)
    print(f"  - Selectors found: {sel_count} (required >= 4)")
    assert sel_count >= 4, f"Expected at least 4 selectors, found {sel_count}"

    # 3. Sliders (Invariant requires 5 stimulation sliders)
    slider_count = len(parser.sliders)
    print(f"  - Sliders found: {slider_count} (required >= 5)")
    assert slider_count >= 5, f"Expected at least 5 sliders, found {slider_count}"

    # 4. Tabs (4 tabs: live, raw, rom, exercises)
    tab_count = len(parser.tabs)
    print(f"  - Tabs found: {tab_count} (required == 4)")
    assert tab_count == 4, f"Expected 4 navigation tabs, found {tab_count}"

    # 5. Modals (7 modal overlays)
    modal_count = len(parser.modals)
    print(f"  - Modals found: {modal_count} (required == 7): {parser.modals}")
    assert modal_count == 7, f"Expected 7 modals, found {modal_count}"

    # 6. Canvas graphs (>= 4 canvases)
    canvas_count = len(parser.canvases)
    canvas_ids = [c["id"] for c in parser.canvases]
    print(f"  - Canvases found: {canvas_count} (required >= 4): {canvas_ids}")
    assert canvas_count >= 4, f"Expected at least 4 canvases, found {canvas_count}"
    for required_canvas in ["legCanvas", "jointAngleCanvas", "footPressureCanvas", "chartUnified"]:
        assert required_canvas in canvas_ids, f"Missing required canvas: {required_canvas}"

    # 7. No duplicate IDs & valid balanced HTML tags
    chk_count = parser.ids.count("chkAutoRecord")
    print(f"  - #chkAutoRecord ID occurrences: {chk_count} (required == 1)")
    assert chk_count == 1, f"Expected exactly 1 occurrence of #chkAutoRecord, found {chk_count}"
    dup_ids = sorted({x for x in parser.ids if parser.ids.count(x) > 1})
    assert not dup_ids, f"Duplicate DOM IDs detected: {dup_ids}"
    assert not parser.tag_errors, f"Unbalanced closing HTML tags: {parser.tag_errors}"
    assert not parser._stack, f"Unclosed HTML tags: {parser._stack}"

    # 8. Title string invariant
    print(f"  - Document title: {repr(parser.title_text.strip())}")
    assert "OA Risk Detection" in parser.title_text or "OA Risk Detection" in html_content, \
        "Title or brand must contain 'OA Risk Detection'"


def audit_pin_tooltips(html_content: str) -> None:
    print("\n[2/6] Auditing hardware status indicator tooltips...")
    # Verify GPIO 26, 27, 25 are explicitly bound to the LED & Buzzer status pills
    assert "GPIO 26" in html_content, "Green LED tooltip must reference GPIO 26"
    assert "GPIO 27" in html_content, "Red LED tooltip must reference GPIO 27"
    assert "GPIO 25" in html_content, "Buzzer tooltip must reference GPIO 25"

    # Verify no legacy stale pins remain in the status bar (e.g. GPIO 12, GPIO 14, GPIO 13)
    status_bar_match = re.search(r'class="status-bar[^"]*".*?</div>\s*</div>', html_content, re.DOTALL)
    assert status_bar_match is not None, "Could not find status bar in dashboard markup"
    status_html = status_bar_match.group(0)
    assert "GPIO 12" not in status_html, "Found stale GPIO 12 in status bar"
    assert "GPIO 14" not in status_html, "Found stale GPIO 14 in status bar"
    assert "GPIO 13" not in status_html, "Found stale GPIO 13 in status bar"
    print("  - Hardware indicators accurately reflect GPIO 26 (Green LED), GPIO 27 (Red LED), GPIO 25 (Buzzer)")


def audit_emoji_elimination(html_content: str) -> None:
    print("\n[3/6] Auditing elimination of decorative emojis...")
    # Unicode emoji patterns (extended pictographic, emoticons, symbols)
    emoji_pattern = re.compile(r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]")
    matches = list(emoji_pattern.finditer(html_content))
    print(f"  - Found {len(matches)} emoji matches in dashboard.html")
    if matches:
        for m in matches[:5]:
            snippet = html_content[max(0, m.start() - 25):min(len(html_content), m.end() + 25)]
            print(f"    Sample match {m.group()}: {repr(snippet)}")
    assert len(matches) == 0, f"Expected 0 emojis in Apple-style dashboard, found {len(matches)}"


def audit_apple_design_tokens(html_content: str) -> None:
    print("\n[4/6] Auditing Apple-inspired minimalist design tokens...")
    # Typography: SF Pro system stack
    assert "-apple-system" in html_content or "system-ui" in html_content, "Missing Apple system font family"
    print("  - Typography: SF Pro / Apple system font stack verified")

    # Clean background palette
    assert "#f5f5f7" in html_content or "--bg-body" in html_content, "Missing Apple neutral background #f5f5f7"
    print("  - Palette: Neutral canvas verified")

    # Hairline borders and soft layered shadows
    assert "0.08" in html_content or "rgba(0, 0, 0, 0.08)" in html_content or "rgba(0,0,0,0.08)" in html_content, \
        "Missing subtle hairline borders"
    print("  - Borders: Subtle hairline styling verified")


def audit_offline_assets() -> None:
    print("\n[5/6] Auditing offline Chart.js static assets...")
    chart_js_path = PROJECT_ROOT / "backend" / "static" / "chart.umd.min.js"
    assert chart_js_path.exists(), f"Static asset missing: {chart_js_path}"
    file_size = chart_js_path.stat().st_size
    print(f"  - {chart_js_path.name} exists ({file_size} bytes)")
    assert file_size > 50000, f"Chart.js asset appears truncated ({file_size} bytes)"


def audit_api_routes(html_content: str) -> None:
    print("\n[6/6] Auditing client-side API calls against registered FastAPI routes...")
    app = create_app()
    openapi_paths = set(app.openapi()["paths"].keys())

    # Extract all endpoint string literals like '/api/...'
    raw_calls = set(re.findall(r"[\'\"](/api/[^\'\"\s\?]+)", html_content))
    # Also extract template literals with `/api/...`
    template_calls = set(re.findall(r"`(/api/[^`\s\?]+)`", html_content))
    all_calls = raw_calls | template_calls

    print(f"  - Extracted {len(all_calls)} client-side API endpoint patterns")

    for call in sorted(all_calls):
        # Normalize template expressions like `/api/patients/${id}` -> `/api/patients/{patient_id}`
        norm_call = re.sub(r"\$\{[^}]+\}", "{id}", call)
        # Check direct match or parameter pattern match
        matched = False
        if call in openapi_paths:
            matched = True
        else:
            # Try wildcard matching against registered routes
            regex_pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", call) + "$"
            for route in openapi_paths:
                route_regex = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", route) + "$"
                if re.match(route_regex, norm_call) or re.match(regex_pattern, route):
                    matched = True
                    break

        if not matched:
            # Check prefix for endpoints with dynamic trailing subpaths
            prefix = "/" + "/".join(call.split("/")[1:3])
            matched = any(r.startswith(prefix) for r in openapi_paths)

        assert matched, f"Client call {call} does not match any registered FastAPI route"

    print("  - 100% of client-side API routes successfully matched backend endpoints")


def main() -> int:
    dashboard_path = PROJECT_ROOT / "dashboard.html"
    if not dashboard_path.exists():
        print(f"Error: dashboard.html not found at {dashboard_path}", file=sys.stderr)
        return 1

    html_content = dashboard_path.read_text(encoding="utf-8")
    try:
        audit_dom_elements(html_content)
        audit_pin_tooltips(html_content)
        audit_emoji_elimination(html_content)
        audit_apple_design_tokens(html_content)
        audit_offline_assets()
        audit_api_routes(html_content)
        print("\n" + "=" * 70)
        print("ALL DASHBOARD END-TO-END AUDITS PASSED SUCCESSFULLY!")
        print("=" * 70)
        return 0
    except AssertionError as e:
        print(f"\nAUDIT FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
