"""Parse lpGBT Python register map classes into structured register data.

Extracts register definitions from lpgbt_control_lib's register map classes,
which contain the canonical register names, addresses, bit fields, and descriptions.
"""

import importlib.util
import inspect
import json
import sys
from pathlib import Path


def load_register_map_module(py_path: Path):
    """Dynamically load a Python register map module."""
    spec = importlib.util.spec_from_file_location("regmap", py_path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot load {py_path}")
    module = importlib.util.module_from_spec(spec)
    # We need the parent package in sys.modules for relative imports
    parent_dir = py_path.parent
    pkg_name = parent_dir.name
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, parent_dir / "__init__.py"
        )
        if pkg_spec and pkg_spec.loader:
            pkg = importlib.util.module_from_spec(pkg_spec)
            sys.modules[pkg_name] = pkg
            try:
                pkg_spec.loader.exec_module(pkg)
            except Exception:
                pass  # Some imports may fail — we only need the register classes
    spec.loader.exec_module(module)
    return module


def extract_registers_from_class(register_map_class, version: str) -> list[dict]:
    """Extract all register definitions from a register map class hierarchy.

    The lpgbt_control_lib register maps use nested classes:
    - Outer class = register (has .address)
    - Inner class = bit field (has .offset, .length, .bit_mask, docstring)
    """
    registers = []

    for reg_name, reg_cls in inspect.getmembers(register_map_class, inspect.isclass):
        if reg_name.startswith("_"):
            continue

        # Must have an address attribute
        address = getattr(reg_cls, "address", None)
        if address is None:
            continue

        # Extract bit fields
        fields = []
        for field_name, field_cls in inspect.getmembers(reg_cls, inspect.isclass):
            if field_name.startswith("_"):
                continue
            offset = getattr(field_cls, "offset", None)
            length = getattr(field_cls, "length", None)
            if offset is None or length is None:
                continue

            description = (field_cls.__doc__ or "").strip()
            # Clean up default value from description
            description = description.split(";")[0].strip() if description else ""

            fields.append({
                "name": field_name,
                "offset": offset,
                "length": length,
                "bit_mask": getattr(field_cls, "bit_mask", None),
                "description": description,
            })

        # Sort fields by offset descending (MSB first, like hardware docs)
        fields.sort(key=lambda f: f["offset"], reverse=True)

        # Register description from docstring
        reg_desc = (reg_cls.__doc__ or "").strip()
        # The __str__ method often has the human-readable name
        display_name = reg_name
        str_method = getattr(reg_cls, "__str__", None)
        if str_method:
            try:
                display_name = str_method()
            except Exception:
                pass

        registers.append({
            "name": display_name,
            "class_name": reg_name,
            "address": address,
            "address_hex": f"0x{address:03X}",
            "version": version,
            "description": reg_desc,
            "fields": fields,
            "fields_json": json.dumps(fields),
        })

    # Sort by address
    registers.sort(key=lambda r: r["address"])
    return registers


def parse_register_maps(lpgbt_control_lib_path: Path) -> dict[str, list[dict]]:
    """Parse all version register maps from lpgbt_control_lib.

    Returns dict keyed by version ('v0', 'v1', 'v2').
    """
    result = {}

    # v0 has its own complete file
    v0_path = lpgbt_control_lib_path / "lpgbt_register_map_v0.py"
    if v0_path.exists():
        try:
            mod = load_register_map_module(v0_path)
            for name, cls in inspect.getmembers(mod, inspect.isclass):
                if "RegisterMap" in name and "V0" in name:
                    result["v0"] = extract_registers_from_class(cls, "v0")
                    break
        except Exception as e:
            print(f"Warning: Could not parse v0 register map: {e}")

    # v1 and v2 inherit from a shared base
    base_path = lpgbt_control_lib_path / "lpgbt_register_map_base_v1v2.py"
    v1_path = lpgbt_control_lib_path / "lpgbt_register_map_v1.py"
    v2_path = lpgbt_control_lib_path / "lpgbt_register_map_v2.py"

    for ver, path in [("v1", v1_path), ("v2", v2_path)]:
        if path.exists():
            try:
                mod = load_register_map_module(path)
                for name, cls in inspect.getmembers(mod, inspect.isclass):
                    if "RegisterMap" in name and ver.upper() in name.upper():
                        result[ver] = extract_registers_from_class(cls, ver)
                        break
            except Exception as e:
                print(f"Warning: Could not parse {ver} register map: {e}")

    return result
