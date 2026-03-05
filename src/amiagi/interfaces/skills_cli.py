"""CLI for skill management: ``amiagi skills list|add|remove|search``.

Can be invoked as:
  python -m amiagi skills list
  python -m amiagi skills add --name code_review --desc "Code review skill"
  python -m amiagi skills remove code_review
  python -m amiagi skills search planning

Uses the PostgreSQL SkillRepository by default (same as Web GUI).
Falls back to the local JSON catalog when DB is unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Sequence

# Re-export for backwards compatibility
from amiagi.application.skill_catalog import SkillCatalog, SkillEntry


# ── DB connection helpers ──────────────────────────────────────

def _load_db_dsn() -> str | None:
    """Read DB DSN from model_config.json or env."""
    import os

    dsn = os.environ.get("AMIAGI_DB_DSN")
    if dsn:
        return dsn

    config_path = Path("data") / "model_config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            return cfg.get("db_dsn") or cfg.get("database_url")
        except Exception:
            pass

    # Fallback: standard local dev DSN
    return "postgresql://zdalny@localhost:5432/amiagi"


async def _get_pool():
    """Create a temporary asyncpg pool for CLI operations."""
    import asyncpg

    dsn = _load_db_dsn()
    if dsn is None:
        return None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        return pool
    except Exception:
        return None


# ── JSON fallback ──────────────────────────────────────────────

def _default_catalog_path() -> Path:
    return Path("data") / "skill_catalog.json"


def _load_catalog(path: Path) -> SkillCatalog:
    catalog = SkillCatalog()
    if path.exists():
        catalog.load_json(path)
    return catalog


def _save_catalog(catalog: SkillCatalog, path: Path) -> None:
    catalog.save_json(path)


# ── DB-backed handlers ────────────────────────────────────────

async def _db_cmd_list(pool) -> None:
    from amiagi.interfaces.web.skills.skill_repository import SkillRepository

    repo = SkillRepository(pool)
    skills = await repo.list_skills(active_only=False)
    if not skills:
        print("No skills registered.")
        return
    for s in skills:
        kw = ", ".join(s.trigger_keywords) if s.trigger_keywords else "-"
        status = "active" if s.is_active else "inactive"
        print(f"  {s.name:30s}  [{s.category}]  keywords: {kw}  ({status})")
    print(f"\nTotal: {len(skills)}")


async def _db_cmd_add(pool, args: argparse.Namespace) -> None:
    from amiagi.interfaces.web.skills.skill_repository import SkillRepository

    repo = SkillRepository(pool)
    tags = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    skill = await repo.create_skill(
        name=args.name,
        display_name=args.name.replace("_", " ").title(),
        description=args.desc or "",
        content=args.desc or "",
        category=args.difficulty or "general",
        trigger_keywords=tags,
    )
    print(f"Registered skill: {skill.name}  (id={skill.id})")


async def _db_cmd_remove(pool, args: argparse.Namespace) -> None:
    from amiagi.interfaces.web.skills.skill_repository import SkillRepository

    repo = SkillRepository(pool)
    # Search by name to get ID
    skills = await repo.list_skills(active_only=False)
    target = next((s for s in skills if s.name == args.name), None)
    if target is None:
        print(f"Skill not found: {args.name}")
        sys.exit(1)
    await repo.delete_skill(target.id)
    print(f"Removed skill: {args.name}")


async def _db_cmd_search(pool, args: argparse.Namespace) -> None:
    from amiagi.interfaces.web.skills.skill_repository import SkillRepository

    repo = SkillRepository(pool)
    query = args.query.lower()
    skills = await repo.list_skills(active_only=False)
    results = [
        s for s in skills
        if query in s.name.lower()
        or query in s.description.lower()
        or any(query in kw.lower() for kw in s.trigger_keywords)
    ]
    if not results:
        print(f"No skills matching '{args.query}'.")
        return
    for s in results:
        print(f"  {s.name:30s}  {s.description[:60]}")
    print(f"\nMatches: {len(results)}")


# ── JSON-backed handlers (fallback) ───────────────────────────

def _cmd_list(catalog: SkillCatalog, _args: argparse.Namespace) -> None:
    skills = catalog.list_skills()
    if not skills:
        print("No skills registered.")
        return
    for skill in skills:
        tags = ", ".join(skill.tags) if skill.tags else "-"
        print(f"  {skill.name:30s}  [{skill.difficulty_level}]  tags: {tags}")
    print(f"\nTotal: {len(skills)}")


def _cmd_add(catalog: SkillCatalog, args: argparse.Namespace) -> None:
    entry = SkillEntry(
        name=args.name,
        description=args.desc or "",
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        difficulty_level=args.difficulty or "medium",
    )
    catalog.register(entry)
    _save_catalog(catalog, args._catalog_path)
    print(f"Registered skill: {entry.name}")


def _cmd_remove(catalog: SkillCatalog, args: argparse.Namespace) -> None:
    removed = catalog.unregister(args.name)
    if removed:
        _save_catalog(catalog, args._catalog_path)
        print(f"Removed skill: {args.name}")
    else:
        print(f"Skill not found: {args.name}")
        sys.exit(1)


def _cmd_search(catalog: SkillCatalog, args: argparse.Namespace) -> None:
    results = catalog.search(args.query)
    if not results:
        print(f"No skills matching '{args.query}'.")
        return
    for skill in results:
        print(f"  {skill.name:30s}  {skill.description[:60]}")
    print(f"\nMatches: {len(results)}")


# ── Entry point ────────────────────────────────────────────────

def build_skills_parser(parent_subparsers=None) -> argparse.ArgumentParser:
    """Build the ``skills`` sub-parser (standalone or nested)."""
    if parent_subparsers is not None:
        parser = parent_subparsers.add_parser("skills", help="Manage skill catalog")
    else:
        parser = argparse.ArgumentParser(prog="amiagi skills", description="Manage skill catalog")

    parser.add_argument(
        "--catalog",
        type=Path,
        default=_default_catalog_path(),
        help="Path to skill_catalog.json (fallback when DB unavailable)",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        default=False,
        help="Force use of local JSON catalog instead of PostgreSQL",
    )

    sub = parser.add_subparsers(dest="skills_command")

    # list
    sub.add_parser("list", help="List all registered skills")

    # add
    add_p = sub.add_parser("add", help="Register a new skill")
    add_p.add_argument("--name", required=True, help="Skill name (unique)")
    add_p.add_argument("--desc", default="", help="Short description")
    add_p.add_argument("--tags", default="", help="Comma-separated tags")
    add_p.add_argument("--difficulty", default="medium", choices=("easy", "medium", "hard"))

    # remove
    rm_p = sub.add_parser("remove", help="Remove a skill")
    rm_p.add_argument("name", help="Skill name to remove")

    # search
    sr_p = sub.add_parser("search", help="Search skills by keyword")
    sr_p.add_argument("query", help="Search query")

    return parser


async def _run_db_command(cmd: str, args: argparse.Namespace) -> bool:
    """Try to execute *cmd* against PostgreSQL. Returns True on success."""
    pool = await _get_pool()
    if pool is None:
        return False
    try:
        if cmd == "list":
            await _db_cmd_list(pool)
        elif cmd == "add":
            await _db_cmd_add(pool, args)
        elif cmd == "remove":
            await _db_cmd_remove(pool, args)
        elif cmd == "search":
            await _db_cmd_search(pool, args)
        else:
            return False
        return True
    finally:
        await pool.close()


def run_skills_cli(argv: Sequence[str] | None = None) -> None:
    """Run the skills CLI with the given arguments."""
    parser = build_skills_parser()
    args = parser.parse_args(argv)

    cmd = args.skills_command
    if not cmd:
        parser.print_help()
        sys.exit(1)

    # Try PostgreSQL first (unless --json-only)
    if not getattr(args, "json_only", False):
        try:
            success = asyncio.run(_run_db_command(cmd, args))
            if success:
                return
            print("⚠ Database unavailable, falling back to local JSON catalog.", file=sys.stderr)
        except Exception:
            print("⚠ Database unavailable, falling back to local JSON catalog.", file=sys.stderr)

    # JSON fallback
    catalog_path = args.catalog
    args._catalog_path = catalog_path
    catalog = _load_catalog(catalog_path)

    if cmd == "list":
        _cmd_list(catalog, args)
    elif cmd == "add":
        _cmd_add(catalog, args)
    elif cmd == "remove":
        _cmd_remove(catalog, args)
    elif cmd == "search":
        _cmd_search(catalog, args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    run_skills_cli()
