#!/usr/bin/env python3
"""Build the lpGBT documentation SQLite index.

Reads HTML manuals (v0, v1, v2) and optionally the Python register map
files from lpgbt_control_lib, then builds a searchable SQLite + FTS5 index.

Usage:
    python3 build_index.py [--html-dir data/html] [--regmap-dir /path/to/lpgbt_control_lib]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from src.lpgbt_docs_mcp.html_parser import parse_html_page, extract_registers_from_html
from src.lpgbt_docs_mcp.register_parser import parse_register_maps


def create_schema(conn: sqlite3.Connection):
    conn.executescript("""
        DROP TABLE IF EXISTS sections;
        DROP TABLE IF EXISTS sections_fts;
        DROP TABLE IF EXISTS registers;

        CREATE TABLE sections (
            id INTEGER PRIMARY KEY,
            version TEXT NOT NULL,
            page TEXT NOT NULL,
            category TEXT NOT NULL,
            heading TEXT NOT NULL,
            summary TEXT,
            markdown TEXT
        );

        CREATE VIRTUAL TABLE sections_fts USING fts5(
            heading, summary, markdown,
            content='sections', content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE TRIGGER sections_ai AFTER INSERT ON sections BEGIN
            INSERT INTO sections_fts(rowid, heading, summary, markdown)
            VALUES (new.id, new.heading, new.summary, new.markdown);
        END;

        CREATE TABLE registers (
            id INTEGER PRIMARY KEY,
            version TEXT NOT NULL,
            name TEXT NOT NULL,
            address INTEGER NOT NULL,
            address_hex TEXT NOT NULL,
            description TEXT,
            fields_json TEXT,
            section_id INTEGER REFERENCES sections(id)
        );

        CREATE INDEX idx_registers_name ON registers(name COLLATE NOCASE);
        CREATE INDEX idx_registers_addr ON registers(version, address);
        CREATE INDEX idx_sections_version ON sections(version);
        CREATE INDEX idx_sections_cat ON sections(category);
    """)


def ingest_html(conn: sqlite3.Connection, html_dir: Path):
    """Parse all HTML pages and insert sections."""
    total_sections = 0
    total_registers = 0

    for version in ("v0", "v1", "v2"):
        ver_dir = html_dir / version
        if not ver_dir.exists():
            print(f"  Skipping {version}: {ver_dir} not found")
            continue

        html_files = sorted(ver_dir.glob("*.html"))
        print(f"  {version}: {len(html_files)} HTML files")

        for html_path in html_files:
            if html_path.stem == "search":
                continue  # Skip Sphinx search page

            chunks = parse_html_page(html_path)
            for chunk in chunks:
                conn.execute(
                    "INSERT INTO sections (version, page, category, heading, summary, markdown) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (version, chunk["page"], chunk["category"], chunk["heading"],
                     chunk["summary"], chunk["markdown"]),
                )
                total_sections += 1

            # Also extract registers from registermap.html
            if html_path.stem == "registermap":
                regs = extract_registers_from_html(html_path, version)
                for reg in regs:
                    conn.execute(
                        "INSERT INTO registers (version, name, address, address_hex, description, fields_json) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (version, reg["name"], reg["address"], reg["address_hex"],
                         reg["description"], json.dumps(reg["fields"])),
                    )
                    total_registers += 1
                print(f"    registermap: {len(regs)} registers extracted")

    print(f"  Total: {total_sections} sections, {total_registers} registers from HTML")
    return total_sections, total_registers


def ingest_python_registers(conn: sqlite3.Connection, regmap_dir: Path):
    """Parse Python register map files for structured register data."""
    reg_maps = parse_register_maps(regmap_dir)
    total = 0
    for version, registers in reg_maps.items():
        # Check if we already have registers from HTML for this version
        existing = conn.execute(
            "SELECT COUNT(*) FROM registers WHERE version = ?", (version,)
        ).fetchone()[0]

        if existing > 0:
            print(f"  {version}: replacing {existing} HTML-extracted registers with {len(registers)} from Python")
            conn.execute("DELETE FROM registers WHERE version = ?", (version,))
        else:
            print(f"  {version}: inserting {len(registers)} registers from Python")

        for reg in registers:
            conn.execute(
                "INSERT INTO registers (version, name, address, address_hex, description, fields_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (version, reg["name"], reg["address"], reg["address_hex"],
                 reg["description"], reg["fields_json"]),
            )
            total += 1

    print(f"  Total: {total} registers from Python register maps")
    return total


def rebuild_fts(conn: sqlite3.Connection):
    """Rebuild the FTS5 index."""
    conn.execute("INSERT INTO sections_fts(sections_fts) VALUES('rebuild')")


def print_stats(conn: sqlite3.Connection):
    """Print index statistics."""
    sections = conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    registers = conn.execute("SELECT COUNT(*) FROM registers").fetchone()[0]
    versions = conn.execute("SELECT version, COUNT(*) FROM sections GROUP BY version").fetchall()
    categories = conn.execute("SELECT category, COUNT(*) FROM sections GROUP BY category").fetchall()
    reg_versions = conn.execute("SELECT version, COUNT(*) FROM registers GROUP BY version").fetchall()

    print(f"\n=== Index Statistics ===")
    print(f"Sections: {sections}")
    print(f"Registers: {registers}")
    print(f"\nSections by version:")
    for v, c in versions:
        print(f"  {v}: {c}")
    print(f"\nSections by category:")
    for v, c in categories:
        print(f"  {v}: {c}")
    print(f"\nRegisters by version:")
    for v, c in reg_versions:
        print(f"  {v}: {c}")


def main():
    parser = argparse.ArgumentParser(description="Build lpGBT documentation index")
    parser.add_argument("--html-dir", type=Path, default=Path("data/html"),
                        help="Directory containing v0/v1/v2 HTML subdirectories")
    parser.add_argument("--regmap-dir", type=Path, default=None,
                        help="Path to lpgbt_control_lib directory with Python register maps")
    parser.add_argument("--out", type=Path, default=Path("data/lpgbt_docs.db"),
                        help="Output SQLite database path")
    args = parser.parse_args()

    if not args.html_dir.exists():
        print(f"ERROR: HTML directory not found: {args.html_dir}")
        sys.exit(1)

    print(f"Building lpGBT documentation index")
    print(f"  HTML source: {args.html_dir}")
    print(f"  Output: {args.out}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(args.out))
    conn.execute("PRAGMA journal_mode=WAL")

    print("\n--- Creating schema ---")
    create_schema(conn)

    print("\n--- Ingesting HTML documentation ---")
    ingest_html(conn, args.html_dir)

    if args.regmap_dir and args.regmap_dir.exists():
        print(f"\n--- Ingesting Python register maps from {args.regmap_dir} ---")
        ingest_python_registers(conn, args.regmap_dir)
    else:
        print("\n--- Skipping Python register maps (use --regmap-dir to include) ---")

    print("\n--- Rebuilding FTS index ---")
    rebuild_fts(conn)

    conn.commit()
    print_stats(conn)
    conn.close()

    db_size = args.out.stat().st_size / (1024 * 1024)
    print(f"\nDatabase: {args.out} ({db_size:.1f} MB)")
    print("Done!")


if __name__ == "__main__":
    main()
