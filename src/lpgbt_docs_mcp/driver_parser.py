"""Parse lpgbt_control_lib Python source files into searchable driver documentation.

Extracts classes, methods (with signatures and docstrings), enumerations,
calibration coefficients, and constants — organized by hardware feature
for controls-oriented querying.
"""

import ast
import re
import textwrap
from pathlib import Path


# Map class/method names to hardware feature categories
FEATURE_MAP = {
    "adc": "adc",
    "vdac": "dac",
    "cdac": "dac",
    "dac": "dac",
    "gpio": "gpio",
    "pio": "gpio",
    "i2c": "i2c_master",
    "fuse": "efuse",
    "temperature": "temperature",
    "temp": "temperature",
    "vref": "voltage_reference",
    "clock": "clocking",
    "clk": "clocking",
    "eclk": "clocking",
    "pll": "clocking",
    "cdr": "clocking",
    "fll": "clocking",
    "phase_shifter": "phase_shifter",
    "eprx": "eport_rx",
    "eptx": "eport_tx",
    "equalizer": "equalizer",
    "eq": "equalizer",
    "line_driver": "line_driver",
    "serializer": "high_speed_link",
    "uplink": "high_speed_link",
    "downlink": "high_speed_link",
    "bert": "testing",
    "eye": "testing",
    "eom": "testing",
    "prbs": "testing",
    "pusm": "power_up",
    "power": "power_monitoring",
    "vdd": "power_monitoring",
    "brownout": "power_monitoring",
    "watchdog": "watchdog",
    "crc": "configuration",
    "config": "configuration",
    "register": "register_access",
    "write_reg": "register_access",
    "read_reg": "register_access",
    "chipid": "chip_id",
    "ready": "configuration",
    "reset": "configuration",
    "hamming": "error_correction",
    "majority": "error_correction",
    "calibrat": "calibration",
    "measure": "calibration",
    "tune": "calibration",
    "resistance": "calibration",
}


def classify_method(method_name: str, class_name: str = "") -> str:
    """Classify a method into a hardware feature category."""
    combined = f"{class_name}.{method_name}".lower()
    for keyword, category in FEATURE_MAP.items():
        if keyword in combined:
            return category
    return "general"


def parse_python_file(filepath: Path) -> list[dict]:
    """Parse a Python file and extract documented classes, methods, constants, and enums.

    Returns list of chunks, each representing a searchable unit.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as e:
        print(f"  Warning: Could not parse {filepath.name}: {e}")
        return []

    chunks = []
    filename = filepath.stem

    # Only process top-level classes (not nested register/field classes)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            chunks.extend(_extract_class(node, filename, source))

    # Also extract module-level constants and assignments
    module_consts = _extract_module_constants(tree, filename)
    if module_consts:
        chunks.append(module_consts)

    return chunks


def _extract_class(cls_node: ast.ClassDef, filename: str, source: str) -> list[dict]:
    """Extract a class definition with its methods."""
    chunks = []
    class_name = cls_node.name
    class_doc = ast.get_docstring(cls_node) or ""
    bases = [_get_name(b) for b in cls_node.bases]

    # Check if this is an Enum class (must inherit directly from Enum/IntEnum, not just contain "Enum" in name)
    is_enum = any(b in ("Enum", "IntEnum", "enum.Enum", "enum.IntEnum") for b in bases)

    if is_enum:
        # Extract enum members
        members = []
        for item in cls_node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        val = _get_literal_value(item.value)
                        members.append(f"- `{target.id}` = {val}")

        if members:
            category = classify_method(class_name)
            markdown = f"## Enum: {class_name}\n\n"
            if class_doc:
                markdown += f"{class_doc}\n\n"
            markdown += f"**Bases:** {', '.join(bases)}\n\n"
            markdown += "**Members:**\n" + "\n".join(members)
            chunks.append({
                "heading": f"Enum: {class_name}",
                "summary": class_doc[:200] if class_doc else f"Enumeration with {len(members)} members",
                "markdown": markdown,
                "category": "driver_enum",
                "page": filename,
                "feature": category,
            })
        return chunks

    # Regular class — extract methods
    methods = []
    for item in cls_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if item.name.startswith("__") and item.name != "__init__":
                continue
            method_info = _extract_method(item, class_name)
            if method_info:
                methods.append(method_info)

    # Group methods by feature for better chunking
    by_feature: dict[str, list[dict]] = {}
    for m in methods:
        feat = m["feature"]
        by_feature.setdefault(feat, []).append(m)

    for feature, feature_methods in by_feature.items():
        markdown_parts = [f"## {class_name} — {feature.replace('_', ' ').title()} Methods\n"]
        if class_doc and feature == classify_method(class_name):
            markdown_parts.append(f"{class_doc}\n")

        for m in feature_methods:
            markdown_parts.append(f"### `{m['signature']}`\n")
            if m["docstring"]:
                markdown_parts.append(f"{m['docstring']}\n")
            if m["params"]:
                markdown_parts.append("**Parameters:**")
                for p in m["params"]:
                    markdown_parts.append(f"- `{p}`")
                markdown_parts.append("")

        heading = f"{class_name}: {feature.replace('_', ' ')} methods"
        summary_methods = ", ".join(m["name"] for m in feature_methods[:5])
        if len(feature_methods) > 5:
            summary_methods += f", ... ({len(feature_methods)} total)"

        chunks.append({
            "heading": heading,
            "summary": f"Methods: {summary_methods}",
            "markdown": "\n".join(markdown_parts),
            "category": "driver",
            "page": filename,
            "feature": feature,
        })

    return chunks


def _extract_method(func_node: ast.FunctionDef, class_name: str) -> dict | None:
    """Extract method signature, docstring, and parameters."""
    name = func_node.name
    docstring = ast.get_docstring(func_node) or ""

    # Build signature
    args = func_node.args
    params = []
    defaults_offset = len(args.args) - len(args.defaults)

    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue
        param_str = arg.arg
        if arg.annotation:
            param_str += f": {_get_name(arg.annotation)}"
        default_idx = i - defaults_offset
        if default_idx >= 0 and default_idx < len(args.defaults):
            default_val = _get_literal_value(args.defaults[default_idx])
            param_str += f"={default_val}"
        params.append(param_str)

    sig = f"{name}({', '.join(params)})"
    feature = classify_method(name, class_name)

    # Skip trivial methods
    if not docstring and not params and name.startswith("_"):
        return None

    return {
        "name": name,
        "signature": sig,
        "docstring": docstring,
        "params": params,
        "feature": feature,
    }


def _extract_module_constants(tree: ast.Module, filename: str) -> dict | None:
    """Extract module-level constants."""
    constants = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    val = _get_literal_value(node.value)
                    constants.append(f"- `{target.id}` = {val}")

    if not constants:
        return None

    return {
        "heading": f"Constants: {filename}",
        "summary": f"{len(constants)} constants defined",
        "markdown": f"## Constants from {filename}\n\n" + "\n".join(constants[:50]),
        "category": "driver_const",
        "page": filename,
        "feature": "general",
    }


def _get_name(node) -> str:
    """Get the name string from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_get_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Constant):
        return repr(node.value)
    return "?"


def _get_literal_value(node) -> str:
    """Get a string representation of a literal value."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return f'"{node.value}"'
        return str(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return f"-{_get_literal_value(node.operand)}"
    if isinstance(node, ast.List):
        return f"[{', '.join(_get_literal_value(e) for e in node.elts)}]"
    if isinstance(node, ast.Dict):
        return "{...}"
    if isinstance(node, ast.Call):
        return f"{_get_name(node.func)}(...)"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _get_name(node)
    return "?"


def parse_calibration_file(filepath: Path) -> list[dict]:
    """Parse calibration files for coefficients and formulas."""
    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []

    chunks = []
    filename = filepath.stem

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Look for calibration coefficient dictionaries
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and "CAL" in target.id.upper():
                            chunks.append({
                                "heading": f"Calibration: {node.name}.{target.id}",
                                "summary": f"Calibration coefficients for {node.name}",
                                "markdown": f"## {node.name}.{target.id}\n\n```python\n{ast.get_source_segment(source, item)}\n```",
                                "category": "driver_calibration",
                                "page": filename,
                                "feature": "calibration",
                            })

            # Extract calibration methods
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    docstring = ast.get_docstring(item) or ""
                    if any(kw in item.name.lower() for kw in ("calibrat", "measure", "tune", "temperature", "resistance", "vref")):
                        method_source = ast.get_source_segment(source, item)
                        if method_source and len(method_source) > 5000:
                            method_source = method_source[:5000] + "\n# ... (truncated)"
                        chunks.append({
                            "heading": f"Calibration method: {node.name}.{item.name}()",
                            "summary": docstring[:200] if docstring else f"Calibration/measurement method",
                            "markdown": f"## {node.name}.{item.name}()\n\n{docstring}\n\n```python\n{method_source}\n```",
                            "category": "driver_calibration",
                            "page": filename,
                            "feature": "calibration",
                        })

    return chunks


def ingest_control_lib(lib_path: Path) -> list[dict]:
    """Ingest all relevant files from lpgbt_control_lib.

    Returns list of section chunks ready for database insertion.
    """
    all_chunks = []

    # Core driver files (in priority order for controls)
    driver_files = [
        "lpgbt.py",                    # Main driver: 150+ methods
        "lpgbt_base_v1v2.py",          # v1/v2 extensions
        "lpgbt_v0.py",                 # v0-specific
        "lpgbt_v1.py",                 # v1-specific
        "lpgbt_v2.py",                 # v2-specific
        "lpgbt_enums.py",              # Common enumerations
        "lpgbt_enums_base_v1v2.py",    # v1/v2 enumerations
        "lpgbt_enums_v0.py",           # v0 enumerations
        "lpgbt_enums_v1.py",           # v1 enumerations
        "lpgbt_enums_v2.py",           # v2 enumerations
        "lpgbt_pins.py",               # Pin definitions
        "lpgbt_pins_base_v1v2.py",     # v1/v2 pins
        "lpgbt_pins_v0.py",            # v0 pins
        "lpgbt_exceptions.py",         # Exception types
        "hamming.py",                   # Error correction utility
        "majority_vote.py",            # Majority voting utility
    ]

    # Calibration files
    calibration_files = [
        "lpgbt_calibrated.py",
        "lpgbt_v1_calibrated.py",
        "lpgbt_v2_calibrated.py",
    ]

    print(f"  Parsing driver files...")
    for fname in driver_files:
        fpath = lib_path / fname
        if fpath.exists():
            chunks = parse_python_file(fpath)
            all_chunks.extend(chunks)
            print(f"    {fname}: {len(chunks)} chunks")

    print(f"  Parsing calibration files...")
    for fname in calibration_files:
        fpath = lib_path / fname
        if fpath.exists():
            chunks = parse_calibration_file(fpath)
            all_chunks.extend(chunks)
            print(f"    {fname}: {len(chunks)} chunks (calibration)")

    print(f"  Total driver chunks: {len(all_chunks)}")
    return all_chunks
