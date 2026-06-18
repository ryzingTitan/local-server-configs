#!/usr/bin/env python3
"""
Convert a Claude.ai conversation export (conversations.json) to Open WebUI
chat import format.

Usage:
    python convert_claude_to_openwebui.py <input.json> <output.json> [options]

Options:
    --model MODEL       Model name for assistant messages (default: claude)
    --skip-empty        Skip conversations where all messages have empty text
    --include-thinking  Include <thinking>...</thinking> blocks extracted from
                        'thinking' content blocks (default: omitted)

Each Claude conversation becomes one Open WebUI chat object. Branching
conversations are resolved by following the longest (deepest) path from root.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone


ROOT_PARENT = "00000000-0000-4000-8000-000000000000"

ROLE_MAP = {
    "human": "user",
    "assistant": "assistant",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def iso_to_unix(iso: str) -> int:
    """Parse an ISO-8601 UTC timestamp and return a Unix epoch (seconds)."""
    iso = iso.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def extract_text(msg: dict, include_thinking: bool = False) -> str:
    """
    Return the best text representation of a message.

    Priority:
    1. Concatenate 'text' blocks from the structured content list.
    2. Fall back to the top-level 'text' field.

    Optionally prepends <thinking>…</thinking> blocks when include_thinking
    is True.
    """
    content_blocks = msg.get("content", [])

    parts = []

    if include_thinking:
        for block in content_blocks:
            if block.get("type") == "thinking":
                thinking_text = block.get("thinking", block.get("text", "")).strip()
                if thinking_text:
                    parts.append(f"<thinking>\n{thinking_text}\n</thinking>")

    text_parts = [
        b.get("text", "")
        for b in content_blocks
        if b.get("type") == "text"
    ]
    joined = "".join(text_parts).strip()

    if joined:
        parts.append(joined)
    else:
        # Fall back to the top-level text field
        fallback = msg.get("text", "").strip()
        if fallback:
            parts.append(fallback)

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Branch resolution
# ---------------------------------------------------------------------------

def resolve_linear_path(messages: list[dict]) -> list[dict]:
    """
    Given a flat list of chat_messages (which may branch), return the ordered
    list of messages along the longest path from root to leaf.

    Strategy:
    - Build a parent→children map.
    - Walk from root; at any fork, prefer the child with more descendants.
    """
    by_uuid = {m["uuid"]: m for m in messages}
    children_of: dict[str, list[str]] = defaultdict(list)
    roots = []

    for m in messages:
        parent = m.get("parent_message_uuid", ROOT_PARENT)
        if parent == ROOT_PARENT or parent not in by_uuid:
            roots.append(m["uuid"])
        else:
            children_of[parent].append(m["uuid"])

    # Count descendants for each node (used to resolve branches)
    descendant_count: dict[str, int] = {}

    def count_descendants(uid: str) -> int:
        if uid in descendant_count:
            return descendant_count[uid]
        kids = children_of.get(uid, [])
        total = len(kids) + sum(count_descendants(k) for k in kids)
        descendant_count[uid] = total
        return total

    for uid in by_uuid:
        count_descendants(uid)

    # Walk from each root along the deepest path; keep the longest overall
    def walk(uid: str, path: list[str]) -> list[str]:
        path = path + [uid]
        kids = children_of.get(uid, [])
        if not kids:
            return path
        best_kid = max(kids, key=lambda k: count_descendants(k))
        return walk(best_kid, path)

    best_path: list[str] = []
    for root_uid in roots:
        path = walk(root_uid, [])
        if len(path) > len(best_path):
            best_path = path

    return [by_uuid[uid] for uid in best_path]


# ---------------------------------------------------------------------------
# Conversation conversion
# ---------------------------------------------------------------------------

def convert_conversation(
    convo: dict,
    model: str,
    include_thinking: bool,
) -> dict | None:
    """Convert a single Claude conversation dict to an Open WebUI chat object."""
    raw_messages = convo.get("chat_messages", [])
    if not raw_messages:
        return None

    ordered = resolve_linear_path(raw_messages)

    # Build the Open WebUI messages dict
    ow_messages: dict[str, dict] = {}
    id_list: list[str] = []  # ordered Open WebUI message IDs

    for msg in ordered:
        role = ROLE_MAP.get(msg.get("sender", ""), msg.get("sender", "user"))
        text = extract_text(msg, include_thinking=include_thinking)
        timestamp = iso_to_unix(msg["created_at"]) if msg.get("created_at") else 0
        uid = msg["uuid"]

        ow_msg: dict = {
            "id": uid,
            "parentId": None,       # filled below
            "childrenIds": [],      # filled below
            "role": role,
            "content": text,
            "timestamp": timestamp,
        }

        if role == "assistant":
            ow_msg["model"] = model
            ow_msg["done"] = True

        ow_messages[uid] = ow_msg
        id_list.append(uid)

    # Wire parent / child links
    for i, uid in enumerate(id_list):
        if i == 0:
            ow_messages[uid]["parentId"] = None
        else:
            prev_uid = id_list[i - 1]
            ow_messages[uid]["parentId"] = prev_uid
            ow_messages[prev_uid]["childrenIds"].append(uid)

    current_id = id_list[-1] if id_list else None

    title = convo.get("name") or "Untitled conversation"
    created_at = iso_to_unix(convo["created_at"]) if convo.get("created_at") else 0
    updated_at = iso_to_unix(convo["updated_at"]) if convo.get("updated_at") else 0

    return {
        "chat": {
            "title": title,
            "models": [model],
            "history": {
                "currentId": current_id,
                "messages": ow_messages,
            },
        },
        "meta": {
            "tags": [],
        },
        "pinned": False,
        "folder_id": None,
        "created_at": created_at,
        "updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a Claude.ai conversations.json export to Open WebUI format."
    )
    parser.add_argument("input", help="Path to conversations.json")
    parser.add_argument("output", help="Path for the converted output JSON")
    parser.add_argument(
        "--model",
        default="claude",
        help='Model name to embed in assistant messages (default: "claude")',
    )
    parser.add_argument(
        "--skip-empty",
        action="store_true",
        help="Skip conversations where every message has empty text",
    )
    parser.add_argument(
        "--include-thinking",
        action="store_true",
        help="Prepend <thinking> blocks to assistant messages that have them",
    )
    args = parser.parse_args()

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            conversations = json.load(f)
    except FileNotFoundError:
        print(f"Error: '{args.input}' not found.", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: failed to parse JSON — {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(conversations, list):
        print("Error: expected a JSON array at the top level.", file=sys.stderr)
        sys.exit(1)

    results = []
    skipped = 0

    for convo in conversations:
        if args.skip_empty:
            msgs = convo.get("chat_messages", [])
            all_empty = all(
                not extract_text(m, include_thinking=False).strip()
                for m in msgs
            )
            if all_empty:
                skipped += 1
                continue

        converted = convert_conversation(
            convo,
            model=args.model,
            include_thinking=args.include_thinking,
        )
        if converted is not None:
            results.append(converted)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Converted {len(results)} conversation(s) → '{args.output}'")
    if skipped:
        print(f"Skipped {skipped} empty conversation(s)")
    print(f"Model: {args.model}")


if __name__ == "__main__":
    main()