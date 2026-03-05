#!/usr/bin/env python3
"""Migrate skill .md files from ``skills/`` → PostgreSQL ``dbo.skills``.

Usage::

    python scripts/migrate_skills_to_db.py [--dry-run]

Reads all ``*.md`` files under ``skills/<agent_role>/`` and inserts them
into ``dbo.skills``.  Idempotent — uses ``ON CONFLICT (name) DO UPDATE``.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

logger = logging.getLogger(__name__)

# ── Skill file parsing ─────────────────────────────────────────────

_TOOL_RE = re.compile(r"^- `(\w+)`", re.MULTILINE)


def _parse_skill_md(path: Path, agent_role: str) -> dict:
    """Parse a skill markdown file into a dict suitable for DB insert."""
    text = path.read_text(encoding="utf-8")
    lines = text.strip().splitlines()

    # Title from first H1
    name = path.stem  # e.g. "python_development"
    display_name = name.replace("_", " ").title()
    if lines and lines[0].startswith("# "):
        display_name = lines[0][2:].strip()

    # Extract tool names from "- `tool_name`" lines
    tools = _TOOL_RE.findall(text)

    # Rough keyword extraction: take words ≥ 4 chars from first paragraph
    first_para = ""
    for line in lines[1:]:
        if line.startswith("##"):
            break
        first_para += " " + line
    keywords = list({w.lower() for w in re.findall(r"\b\w{4,}\b", first_para)})[:10]

    # Token cost estimate: ~4 chars per token
    token_cost = max(1, len(text) // 4)

    return {
        "id": str(uuid4()),
        "name": name,
        "display_name": display_name,
        "category": agent_role,
        "description": first_para.strip()[:200],
        "content": text,
        "trigger_keywords": keywords,
        "compatible_tools": tools,
        "compatible_roles": [agent_role],
        "token_cost": token_cost,
        "priority": 50,
        "is_active": True,
        "version": 1,
    }


def discover_skills(skills_dir: Path) -> list[dict]:
    """Walk ``skills/<role>/`` and collect parsed skill dicts."""
    results: list[dict] = []
    if not skills_dir.exists():
        logger.warning("Skills directory not found: %s", skills_dir)
        return results

    for role_dir in sorted(skills_dir.iterdir()):
        if not role_dir.is_dir():
            continue
        agent_role = role_dir.name
        for md_file in sorted(role_dir.glob("*.md")):
            if md_file.name.lower() == "readme.md":
                continue
            try:
                skill = _parse_skill_md(md_file, agent_role)
                results.append(skill)
                logger.info("Parsed: %s/%s → %s", agent_role, md_file.name, skill["display_name"])
            except Exception as exc:
                logger.error("Failed to parse %s: %s", md_file, exc)
    return results


# ── Database insertion ─────────────────────────────────────────────

async def insert_skills(skills: list[dict], dsn: str, dry_run: bool = False) -> int:
    """Insert skills into PostgreSQL. Returns count of upserted rows."""
    if dry_run:
        for s in skills:
            print(f"  [DRY-RUN] {s['category']}/{s['name']}: {s['display_name']} "
                  f"({s['token_cost']} tokens, {len(s['trigger_keywords'])} keywords)")
        return len(skills)

    import asyncpg  # type: ignore[import-untyped]

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    now = datetime.now(timezone.utc)
    count = 0

    try:
        async with pool.acquire() as conn:
            for s in skills:
                await conn.execute(
                    """
                    INSERT INTO dbo.skills
                        (id, name, display_name, category, description, content,
                         trigger_keywords, compatible_tools, compatible_roles,
                         token_cost, priority, is_active, version, created_at, updated_at)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    ON CONFLICT (name) DO UPDATE
                        SET display_name = EXCLUDED.display_name,
                            category = EXCLUDED.category,
                            description = EXCLUDED.description,
                            content = EXCLUDED.content,
                            trigger_keywords = EXCLUDED.trigger_keywords,
                            compatible_tools = EXCLUDED.compatible_tools,
                            compatible_roles = EXCLUDED.compatible_roles,
                            token_cost = EXCLUDED.token_cost,
                            version = dbo.skills.version + 1,
                            updated_at = $15
                    """,
                    s["id"], s["name"], s["display_name"], s["category"],
                    s["description"], s["content"],
                    s["trigger_keywords"], s["compatible_tools"], s["compatible_roles"],
                    s["token_cost"], s["priority"], s["is_active"], s["version"],
                    now, now,
                )
                count += 1
                logger.info("Upserted: %s/%s", s["category"], s["name"])
    finally:
        await pool.close()

    return count


# ── CLI entry point ────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate skill files to PostgreSQL")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    parser.add_argument("--skills-dir", default="skills", help="Path to skills/ directory")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN (default: from env)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    skills_dir = Path(args.skills_dir).resolve()
    dsn = args.dsn or os.getenv(
        "DATABASE_URL",
        "postgresql://zdalny:11Cgtr--@localhost:5432/amiagi",
    )

    skills = discover_skills(skills_dir)
    if not skills:
        print("No skills found.")
        return

    print(f"Found {len(skills)} skill(s). Inserting into DB…")
    count = asyncio.run(insert_skills(skills, dsn, dry_run=args.dry_run))
    print(f"Done — {count} skill(s) processed.")


if __name__ == "__main__":
    main()
