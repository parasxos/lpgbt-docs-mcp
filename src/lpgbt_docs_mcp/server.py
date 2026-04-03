"""lpGBT Documentation MCP Server.

Provides full-text search and retrieval across all versions (v0, v1, v2)
of the lpGBT chip manual, including register maps, analog peripherals,
configuration, electrical characteristics, and calibration data.
"""

from mcp.server.fastmcp import FastMCP
from .db import LpgbtDocsDB

mcp = FastMCP("lpgbt-docs")
db = LpgbtDocsDB()


@mcp.tool()
def search_docs(query: str, version: str = "all", max_results: int = 10) -> str:
    """Search across all lpGBT documentation pages.

    Returns section headings, versions, and text snippets matching the query.
    Covers all chip documentation: register map, analog peripherals (ADC, DAC,
    temperature sensor), configuration, clocking, eLinks, electrical specs, etc.

    Args:
        query: Search terms (e.g. "ADC conversion", "temperature calibration",
               "I2C slave write", "clock generator PLL", "ePort phase")
        version: Filter by chip version: "v0", "v1", "v2", or "all" (default)
        max_results: Number of results to return (default 10, max 50)
    """
    results = db.search(query, version, max_results)
    if not results:
        return f"No results found for '{query}' (version={version}). Try broader terms."

    lines = [f"Found {len(results)} result(s) for '{query}' (version={version}):\n"]
    for r in results:
        lines.append(f"**[{r['version']}] {r['heading']}** (ID: {r['id']}, page: {r['page']}, cat: {r['category']})")
        if r["summary"]:
            lines.append(f"  {r['summary'][:200]}")
        if r.get("snippet"):
            lines.append(f"  > {r['snippet']}")
        lines.append("")
    lines.append("Use get_section(id) to read the full content of any result.")
    return "\n".join(lines)


@mcp.tool()
def get_register(name: str, version: str = "all") -> str:
    """Look up an lpGBT register by name or hex address.

    Returns register address, bit fields, reset values, and descriptions.
    Searches across all chip versions by default.

    Args:
        name: Register name (e.g. "CLKGCONFIG1", "ADCCTRL") or hex address
              (e.g. "0x021"). Case-insensitive. Partial matches supported.
        version: Filter by chip version: "v0", "v1", "v2", or "all" (default)
    """
    results = db.get_register(name, version)
    if not results:
        return f"Register '{name}' not found (version={version}). Try a partial name or use search_docs."

    lines = [f"Found {len(results)} register match(es) for '{name}':\n"]
    for r in results:
        lines.append(f"### [{r['version']}] {r['name']} (0x{r['address']:03X})")
        if r.get("description"):
            lines.append(f"{r['description']}")
        if r.get("fields_json"):
            lines.append("\n| Bits | Field | Description |")
            lines.append("|------|-------|-------------|")
            import json
            try:
                fields = json.loads(r["fields_json"])
                for f in fields:
                    bits = f"{f['offset']+f['length']-1}:{f['offset']}" if f["length"] > 1 else str(f["offset"])
                    lines.append(f"| [{bits}] | {f['name']} | {f.get('description', '')} |")
            except (json.JSONDecodeError, KeyError):
                pass
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_section(section_id: int) -> str:
    """Retrieve a full lpGBT documentation section by its ID.

    Use after search_docs returns section IDs to read the complete content.
    Sections are returned as clean Markdown.

    Args:
        section_id: The numeric section ID from search results
    """
    section = db.get_section(section_id)
    if not section:
        return f"Section ID {section_id} not found."

    header = f"# [{section['version']}] {section['heading']}\n"
    header += f"*Page: {section['page']}, Category: {section['category']}*\n\n"
    content = section.get("markdown") or ""

    if len(content) > 12000:
        content = content[:12000] + "\n\n---\n*[Section truncated at 12,000 characters. Use search_docs to find specific subsections.]*"

    return header + content


@mcp.tool()
def list_sections(version: str = "all", category: str = "") -> str:
    """Browse lpGBT documentation sections by version and category.

    Lists section headings and IDs that can be retrieved with get_section.

    Args:
        version: Filter by chip version: "v0", "v1", "v2", or "all" (default)
        category: Filter by type. One of:
                  "analog" - ADC, DAC, temperature sensor, calibration
                  "config" - Configuration, I2C, e-fuses, chip address
                  "register_map" - Register definitions
                  "clocking" - Clock generator, phase shifter, PLL
                  "links" - High-speed links, eLinks, ePorts, line driver
                  "electrical" - Electrical characteristics, package, radiation
                  "general" - Introduction, quick start, FAQ, DFT
                  "" - All categories (default, limited to 100)
    """
    sections = db.list_sections(version, category)
    if not sections:
        return f"No sections found (version={version}, category={category})."

    stats = db.stats()
    lines = [f"lpGBT Documentation Index ({stats['total_sections']} sections, {stats['total_registers']} registers)\n"]

    if not category:
        lines.append("Versions: " + ", ".join(f"{v}: {c}" for v, c in sorted(stats["by_version"].items())))
        lines.append("Categories: " + ", ".join(f"{v}: {c}" for v, c in sorted(stats["by_category"].items())))
        lines.append(f"\nShowing first {len(sections)} sections:\n")

    for s in sections:
        lines.append(f"- [{s['id']}] **[{s['version']}] {s['heading']}** ({s['page']}, {s['category']})")

    return "\n".join(lines)


@mcp.tool()
def compare_versions(topic: str, versions: str = "v0,v1,v2") -> str:
    """Compare lpGBT documentation across chip versions for a given topic.

    Searches the same topic in each requested version and returns results
    side by side, making it easy to identify differences.

    Args:
        topic: The topic to compare (e.g. "ADC configuration", "clock generator",
               "I2C slave", "ePort groups")
        versions: Comma-separated version list (default: "v0,v1,v2")
    """
    ver_list = [v.strip() for v in versions.split(",")]
    results = db.compare_versions(topic, ver_list)

    lines = [f"## Version comparison for '{topic}'\n"]
    for ver in ver_list:
        hits = results.get(ver, [])
        lines.append(f"### {ver.upper()} ({len(hits)} matches)")
        if not hits:
            lines.append("  *No results found*\n")
            continue
        for r in hits:
            lines.append(f"- **{r['heading']}** (ID: {r['id']}, page: {r['page']})")
            if r["summary"]:
                lines.append(f"  {r['summary'][:200]}")
        lines.append("")

    lines.append("Use get_section(id) to read and compare the full content side by side.")
    return "\n".join(lines)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
