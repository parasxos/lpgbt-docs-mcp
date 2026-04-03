"""Parse Sphinx-generated lpGBT HTML documentation into searchable chunks."""

import re
from pathlib import Path
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md


# Page slug -> category mapping
PAGE_CATEGORIES = {
    "registermap": "register_map",
    "analog": "analog",
    "configuration": "config",
    "clkgen": "clocking",
    "phaseShifter": "clocking",
    "highSpeedLinks": "links",
    "ePorts": "links",
    "lineDriver": "links",
    "equalizer": "links",
    "introduction": "general",
    "quickStart": "general",
    "faq": "general",
    "dft": "general",
    "pio": "general",
    "i2cMasters": "config",
    "powerup": "config",
    "electricalCharacteristics": "electrical",
    "model": "electrical",
    "package": "electrical",
    "radiation": "electrical",
    "versionHistory": "general",
    "changelog": "general",
    "credits": "general",
    "known_issues": "general",
    "search": "general",
}

MAX_CHUNK_CHARS = 4000


def classify_page(page_slug: str) -> str:
    return PAGE_CATEGORIES.get(page_slug, "general")


def parse_html_page(html_path: Path) -> list[dict]:
    """Parse a single HTML page into heading-based chunks.

    Returns list of dicts with: heading, summary, markdown, category, page.
    """
    page_slug = html_path.stem
    category = classify_page(page_slug)

    with open(html_path, "rb") as f:
        soup = BeautifulSoup(f, "lxml")

    # Remove navigation, scripts, stylesheets
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()

    # Find the main content area (Sphinx uses div.document or div.body)
    body = soup.find("div", class_="document") or soup.find("div", class_="body") or soup.body
    if not body:
        return []

    chunks = []
    # Split on h2 and h3 headings
    headings = body.find_all(["h1", "h2", "h3"])

    if not headings:
        # No headings — treat the whole page as one chunk
        text = body.get_text(separator=" ", strip=True)
        markdown = _clean_markdown(md(str(body), heading_style="ATX"))
        chunks.append({
            "heading": page_slug,
            "summary": text[:200],
            "markdown": markdown[:MAX_CHUNK_CHARS * 3],
            "category": category,
            "page": page_slug,
        })
        return chunks

    for i, heading in enumerate(headings):
        # Collect content until the next heading of same or higher level
        heading_level = int(heading.name[1])
        content_parts = []
        sibling = heading.next_sibling

        while sibling:
            if isinstance(sibling, Tag):
                if sibling.name in ("h1", "h2", "h3"):
                    sib_level = int(sibling.name[1])
                    if sib_level <= heading_level:
                        break
                content_parts.append(str(sibling))
            sibling = sibling.next_sibling

        heading_text = heading.get_text(strip=True).rstrip("¶")
        content_html = "".join(content_parts)
        plain_text = BeautifulSoup(content_html, "lxml").get_text(separator=" ", strip=True)
        markdown = _clean_markdown(md(content_html, heading_style="ATX"))

        if not plain_text.strip():
            continue

        # Split oversized chunks on h4 boundaries
        if len(markdown) > MAX_CHUNK_CHARS * 2:
            sub_chunks = _split_on_subheadings(heading_text, content_html, category, page_slug)
            if sub_chunks:
                chunks.extend(sub_chunks)
                continue

        chunks.append({
            "heading": heading_text,
            "summary": plain_text[:200],
            "markdown": markdown[:MAX_CHUNK_CHARS * 3],
            "category": category,
            "page": page_slug,
        })

    return chunks


def _split_on_subheadings(parent_heading: str, html: str, category: str, page_slug: str) -> list[dict]:
    """Split large content on h4/h5 boundaries."""
    soup = BeautifulSoup(html, "lxml")
    subheadings = soup.find_all(["h4", "h5"])
    if not subheadings:
        return []

    chunks = []
    for i, sh in enumerate(subheadings):
        parts = []
        sibling = sh.next_sibling
        while sibling:
            if isinstance(sibling, Tag) and sibling.name in ("h4", "h5"):
                break
            parts.append(str(sibling))
            sibling = sibling.next_sibling

        heading_text = f"{parent_heading} > {sh.get_text(strip=True).rstrip('¶')}"
        content_html = "".join(parts)
        plain_text = BeautifulSoup(content_html, "lxml").get_text(separator=" ", strip=True)
        markdown = _clean_markdown(md(content_html, heading_style="ATX"))

        if plain_text.strip():
            chunks.append({
                "heading": heading_text,
                "summary": plain_text[:200],
                "markdown": markdown[:MAX_CHUNK_CHARS * 3],
                "category": category,
                "page": page_slug,
            })

    return chunks


def _clean_markdown(text: str) -> str:
    """Clean up markdownify output."""
    # Remove excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove Sphinx permalink characters
    text = text.replace("¶", "")
    # Remove empty links
    text = re.sub(r"\[]\([^)]*\)", "", text)
    return text.strip()


def extract_registers_from_html(html_path: Path, version: str) -> list[dict]:
    """Extract register definitions from registermap.html.

    Parses the Sphinx register tables into structured data.
    """
    with open(html_path, "rb") as f:
        soup = BeautifulSoup(f, "lxml")

    registers = []
    # lpGBT register map uses h4 headings like "[0xXXX] RegisterName"
    # or h3/h4 with register groups, and nested lists for bit fields
    for heading in soup.find_all(["h3", "h4"]):
        text = heading.get_text(strip=True).rstrip("¶")
        # Match patterns like "[0x021] CLKGConfig1" or "CLKGConfig1 (0x021)"
        m = re.match(r"\[?(0x[0-9A-Fa-f]+)\]?\s*(.+)", text)
        if not m:
            m = re.match(r"(.+?)\s*\(?(0x[0-9A-Fa-f]+)\)?", text)
            if m:
                name, addr_str = m.group(1).strip(), m.group(2)
            else:
                continue
        else:
            addr_str, name = m.group(1), m.group(2).strip()

        try:
            address = int(addr_str, 16)
        except ValueError:
            continue

        # Collect description and fields from siblings
        description_parts = []
        fields = []
        sibling = heading.next_sibling
        while sibling:
            if isinstance(sibling, Tag):
                if sibling.name in ("h3", "h4"):
                    break
                # Extract bit field info from list items
                for li in sibling.find_all("li"):
                    li_text = li.get_text(strip=True)
                    # Pattern: "Bit 7:4 - FieldName[3:0] - Description"
                    fm = re.match(
                        r"Bit\s+(\d+)(?::(\d+))?\s*[-–—]\s*(\w+)(?:\[.*?\])?\s*[-–—:]\s*(.*)",
                        li_text,
                    )
                    if fm:
                        hi = int(fm.group(1))
                        lo = int(fm.group(2)) if fm.group(2) else hi
                        fields.append({
                            "name": fm.group(3),
                            "offset": lo,
                            "length": hi - lo + 1,
                            "description": fm.group(4).strip(),
                        })
                    else:
                        description_parts.append(li_text)
                # Also get paragraph descriptions
                for p in sibling.find_all("p", recursive=False):
                    description_parts.append(p.get_text(strip=True))
            sibling = sibling.next_sibling

        registers.append({
            "name": name,
            "address": address,
            "address_hex": addr_str,
            "version": version,
            "description": " ".join(description_parts)[:500],
            "fields": fields,
        })

    return registers
