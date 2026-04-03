"""Read-only access to the pre-built lpGBT documentation index."""

import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).parent.parent.parent / "data" / "lpgbt_docs.db"


class LpgbtDocsDB:
    def __init__(self, db_path: str | Path | None = None):
        path = db_path or DEFAULT_DB_PATH
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def search(self, query: str, version: str = "all", max_results: int = 10) -> list[dict]:
        """FTS5 search across documentation sections.

        Title matches boosted 10x. Optionally filter by version (v0/v1/v2).
        """
        max_results = min(max_results, 50)
        safe_query = self._sanitize_fts_query(query)
        ver_filter = "" if version == "all" else "AND s.version = ?"
        params: list = [safe_query]
        if version != "all":
            params.append(version)
        params.append(max_results)

        try:
            rows = self.conn.execute(
                f"""
                SELECT s.id, s.version, s.page, s.category, s.heading, s.summary,
                       snippet(sections_fts, 2, '**', '**', '...', 40) as snippet
                FROM sections_fts
                JOIN sections s ON s.id = sections_fts.rowid
                WHERE sections_fts MATCH ?
                {ver_filter}
                ORDER BY bm25(sections_fts, 10.0, 5.0, 1.0)
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        except sqlite3.OperationalError:
            terms = query.split()
            if not terms:
                return []
            or_query = " OR ".join(f'"{t}"' for t in terms)
            params = [or_query]
            if version != "all":
                params.append(version)
            params.append(max_results)
            rows = self.conn.execute(
                f"""
                SELECT s.id, s.version, s.page, s.category, s.heading, s.summary,
                       snippet(sections_fts, 2, '**', '**', '...', 40) as snippet
                FROM sections_fts
                JOIN sections s ON s.id = sections_fts.rowid
                WHERE sections_fts MATCH ?
                {ver_filter}
                ORDER BY bm25(sections_fts, 10.0, 5.0, 1.0)
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_register(self, name: str, version: str = "all") -> list[dict]:
        """Look up register by name or hex address. Returns matches across versions."""
        ver_filter = "" if version == "all" else "AND r.version = ?"
        params: list = []

        # Try by name first
        if name.startswith("0x") or name.startswith("0X"):
            try:
                addr = int(name, 16)
                params = [addr]
                if version != "all":
                    params.append(version)
                rows = self.conn.execute(
                    f"SELECT * FROM registers WHERE address = ? {ver_filter} ORDER BY version",
                    tuple(params),
                ).fetchall()
            except ValueError:
                rows = []
        else:
            params = [name]
            if version != "all":
                params.append(version)
            rows = self.conn.execute(
                f"SELECT * FROM registers WHERE name = ? COLLATE NOCASE {ver_filter} ORDER BY version",
                tuple(params),
            ).fetchall()

        if not rows:
            # Fuzzy: LIKE search
            params = [f"%{name}%"]
            if version != "all":
                params.append(version)
            rows = self.conn.execute(
                f"SELECT * FROM registers WHERE name LIKE ? COLLATE NOCASE {ver_filter} ORDER BY version, name LIMIT 20",
                tuple(params),
            ).fetchall()

        return [dict(r) for r in rows]

    def get_section(self, section_id: int) -> dict | None:
        """Retrieve a full documentation section by ID."""
        row = self.conn.execute(
            "SELECT * FROM sections WHERE id = ?", (section_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_sections(self, version: str = "all", category: str = "", limit: int = 100) -> list[dict]:
        """List section headings, optionally filtered."""
        conditions = []
        params: list = []
        if version != "all":
            conditions.append("version = ?")
            params.append(version)
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT id, version, page, category, heading, summary FROM sections {where} ORDER BY version, page, id LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]

    def compare_versions(self, topic: str, versions: list[str]) -> dict[str, list[dict]]:
        """Search the same topic across multiple versions for comparison."""
        result = {}
        for ver in versions:
            result[ver] = self.search(topic, version=ver, max_results=5)
        return result

    def stats(self) -> dict:
        """Return index statistics."""
        sections = self.conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
        registers = self.conn.execute("SELECT COUNT(*) FROM registers").fetchone()[0]
        versions = self.conn.execute(
            "SELECT version, COUNT(*) FROM sections GROUP BY version"
        ).fetchall()
        categories = self.conn.execute(
            "SELECT category, COUNT(*) FROM sections GROUP BY category"
        ).fetchall()
        return {
            "total_sections": sections,
            "total_registers": registers,
            "by_version": {r[0]: r[1] for r in versions},
            "by_category": {r[0]: r[1] for r in categories},
        }

    def _sanitize_fts_query(self, query: str) -> str:
        cleaned = query.replace("*", "").replace("(", "").replace(")", "")
        terms = cleaned.split()
        if not terms:
            return '""'
        if len(terms) == 1:
            return f'"{terms[0]}"'
        return " ".join(f'"{t}"' for t in terms if t)

    def close(self):
        self.conn.close()
