#!/usr/bin/env python3
"""
Convert a Gemini Apps activity JSON export to Open WebUI chat import format.

Usage:
    python convert_gemini_to_openwebui.py <input.json> <output.json> --title "My Conversation"

Each entry in the Gemini export is treated as a user→assistant exchange:
  - The user prompt is extracted from the "title" field (strips the leading "Prompted " prefix).
  - The assistant response is taken from the first safeHtmlItem's "html" field.

Entries are sorted oldest-first by the "time" field before building the message chain.
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# HTML → plain text helpers
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML-to-text converter that preserves basic structure."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._block_tags = {
            "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
            "li", "br", "tr",
        }

    def handle_starttag(self, tag, attrs):
        if tag in self._block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._block_tags:
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def html_to_text(html: str) -> str:
    """Convert an HTML string to clean plain text."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    raw = stripper.get_text()
    # Collapse runs of blank lines to a single blank line
    lines = raw.splitlines()
    cleaned: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = line.strip() == ""
        if is_blank and prev_blank:
            continue
        cleaned.append(line)
        prev_blank = is_blank
    return "\n".join(cleaned)


# ---------------------------------------------------------------------------
# Conversion logic
# ---------------------------------------------------------------------------

_PROMPTED_PREFIX = "Prompted "


def _extract_user_prompt(title: str) -> str:
    """Strip the 'Prompted ' prefix that Gemini prepends to titles."""
    if title.startswith(_PROMPTED_PREFIX):
        return title[len(_PROMPTED_PREFIX):]
    return title


def _iso_to_unix(iso: str) -> int:
    """Parse an ISO-8601 timestamp and return a UTC Unix timestamp (seconds)."""
    # Python 3.11+ handles 'Z' natively; for older versions replace it.
    iso = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())


def convert(entries: list[dict], title: str, model: str = "gemini") -> list[dict]:
    """
    Convert a list of Gemini activity entries to Open WebUI chat format.

    Parameters
    ----------
    entries : list[dict]
        Parsed contents of the Gemini export JSON.
    title : str
        Conversation title to embed in the output.
    model : str
        Model name to embed in assistant messages (default: "gemini").

    Returns
    -------
    list[dict]
        A single-element list containing the Open WebUI chat object.
    """
    # Sort oldest → newest so the message chain reads chronologically.
    sorted_entries = sorted(entries, key=lambda e: e.get("time", ""))

    messages: dict[str, dict] = {}
    id_chain: list[tuple[str, str]] = []  # [(user_id, assistant_id), ...]

    for entry in sorted_entries:
        raw_title = entry.get("title", "")
        user_content = _extract_user_prompt(raw_title)

        html_items = entry.get("safeHtmlItem", [])
        html = html_items[0].get("html", "") if html_items else ""
        assistant_content = html_to_text(html) if html else ""

        timestamp = _iso_to_unix(entry["time"]) if entry.get("time") else 0

        user_id = str(uuid.uuid4())
        asst_id = str(uuid.uuid4())

        id_chain.append((user_id, asst_id))

        messages[user_id] = {
            "id": user_id,
            "parentId": None,       # filled in below
            "childrenIds": [asst_id],
            "role": "user",
            "content": user_content,
            "timestamp": timestamp,
        }

        messages[asst_id] = {
            "id": asst_id,
            "parentId": user_id,
            "childrenIds": [],      # filled in below
            "role": "assistant",
            "content": assistant_content,
            "model": model,
            "done": True,
            "timestamp": timestamp + 1,  # slightly after the user message
        }

    # Wire up parent/child links across turns
    for i, (user_id, asst_id) in enumerate(id_chain):
        if i == 0:
            messages[user_id]["parentId"] = None
        else:
            prev_asst_id = id_chain[i - 1][1]
            messages[user_id]["parentId"] = prev_asst_id
            messages[prev_asst_id]["childrenIds"] = [user_id]

    # The currentId is the very last assistant message
    current_id = id_chain[-1][1] if id_chain else None

    # Timestamps for the conversation wrapper
    first_ts = messages[id_chain[0][0]]["timestamp"] if id_chain else 0
    last_ts = messages[id_chain[-1][1]]["timestamp"] if id_chain else 0

    chat_obj = {
        "chat": {
            "title": title,
            "models": [model],
            "history": {
                "currentId": current_id,
                "messages": messages,
            },
        },
        "meta": {
            "tags": [],
        },
        "pinned": False,
        "folder_id": None,
        "created_at": first_ts,
        "updated_at": last_ts,
    }

    return [chat_obj]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert a Gemini Apps activity JSON export to Open WebUI chat format."
    )
    parser.add_argument("input", help="Path to the Gemini export JSON file.")
    parser.add_argument("output", help="Path to write the converted Open WebUI JSON.")
    parser.add_argument(
        "--title",
        required=True,
        help='Conversation title (e.g. "RaceBox Corvette Setup").',
    )
    parser.add_argument(
        "--model",
        default="gemini",
        help='Model name to embed in assistant messages (default: "gemini").',
    )
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except FileNotFoundError:
        print(f"Error: input file '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: failed to parse input JSON — {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(entries, list):
        print("Error: expected a JSON array at the top level.", file=sys.stderr)
        sys.exit(1)

    result = convert(entries, title=args.title, model=args.model)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(entries)} entries → '{args.output}'")
    print(f"  Title : {args.title}")
    print(f"  Model : {args.model}")
    print(f"  Messages: {len(entries) * 2} (user + assistant per entry)")


if __name__ == "__main__":
    main()