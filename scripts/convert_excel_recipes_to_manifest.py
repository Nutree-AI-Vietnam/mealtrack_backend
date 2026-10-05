"""Extract and transform recipes from an Excel/CSV file with a 'Structured JSON' column.

Produces a validated manifest JSON ready for import via scripts/import_catalog_recipe_seeds.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NON_FOOD_KEYWORDS = (
    "que xiên",
    "que tre",
    "màng bọc",
    "giấy bạc",
    "giấy nến",
    "tăm",
    "nồi",
    "chảo",
    "khuôn",
    "hộp",
    "dây buộc",
    "chỉ buộc",
    "dụng cụ",
    "tô",
    "bát",
    "chén",
    "bowl",
    "equipment",
    "utensil",
)

PROTEIN_KEYWORDS = (
    "thịt",
    "cá",
    "bacon",
    "giò",
    "chả",
    "gà",
    "bò",
    "heo",
    "tôm",
    "mực",
    "trứng",
    "tofu",
    "đậu hũ",
    "sườn",
    "cua",
    "lươn",
    "ốc",
    "bạch tuộc",
)

PRODUCE_KEYWORDS = (
    "rau",
    "củ",
    "gừng",
    "tỏi",
    "hành",
    "kiệu",
    "ớt",
    "cà chua",
    "nấm",
    "quả",
    "chanh",
    "dưa",
    "xoài",
    "sả",
    "chuối",
    "giá",
    "ngò",
    "húng",
)

SNACK_KEYWORDS = (
    "trà",
    "nước ép",
    "sinh tố",
    "chè",
    "cà phê",
    "ca cao",
    "smoothie",
    "bánh ngọt",
    "thạch",
    "kem",
    "bingsu",
    "pudding",
    "ice cream",
    "gelato",
    "dessert",
    "tráng miệng",
)

BREAKFAST_KEYWORDS = (
    "cháo",
    "bún",
    "phở",
    "miến",
    "bánh mì",
    "xôi",
    "soup",
    "súp",
)


def slugify(text: str) -> str:
    """Convert a Vietnamese title to a safe URL/catalog slug."""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def infer_meal_types(name: str) -> list[str]:
    lower = name.lower()
    if any(k in lower for k in SNACK_KEYWORDS):
        return ["snack"]
    if any(k in lower for k in BREAKFAST_KEYWORDS):
        return ["breakfast", "lunch"]
    return ["lunch", "dinner"]


def parse_instruction_step(text: str, default_index: int) -> dict[str, Any]:
    """Parse 'Bước 1: Sơ chế nguyên liệu Thái phi lê cá...' into step_number, title, description."""
    m = re.match(r"^Bước\s*(\d+)\s*[:\.-]?\s*(.*)", text, re.DOTALL | re.IGNORECASE)
    if m:
        step_number = int(m.group(1))
        content = m.group(2).strip()
    else:
        step_number = default_index
        content = text.strip()

    # Split by explicit punctuation/newline if present
    punc_split = re.split(r"[\n\r]+|(?<=[a-zà-ỹ0-9])[\.\:\-]\s+", content, maxsplit=1)
    if len(punc_split) == 2 and 2 < len(punc_split[0]) < 60:
        return {
            "step_number": step_number,
            "title": punc_split[0].strip(),
            "description": punc_split[1].strip(),
        }

    # Heuristic: Find first capitalized word boundary after 2-5 words for the title
    words = content.split()
    for i in range(2, min(7, len(words))):
        if words[i][0].isupper():
            title = " ".join(words[:i])
            desc = " ".join(words[i:])
            return {
                "step_number": step_number,
                "title": title.strip(),
                "description": desc.strip(),
            }

    return {
        "step_number": step_number,
        "title": f"Bước {step_number}",
        "description": content,
    }


UNIT_PATTERN = r"(kg|g|gram|grams|ml|l|muỗng canh|muỗng cà phê|thìa cà phê|thìa canh|tép|nhánh|củ|trái|quả|gói|hộp|lon|cây|lát|miếng)"


def parse_ingredient_line(raw_line: str) -> dict[str, Any]:
    """Parse 'Phi lê cá: 300g', 'cá mối (500g)', '300g đầu mực', or 'Que xiên: 6 cây'."""
    raw_line = raw_line.strip().lstrip("-*• ")
    if not raw_line:
        return {"is_equipment": True, "text": ""}

    # Detect equipment / utensils first
    if any(
        re.search(rf"(?<!\w){re.escape(k)}(?!\w)", raw_line.casefold())
        for k in NON_FOOD_KEYWORDS
    ):
        return {
            "is_equipment": True,
            "text": raw_line,
        }

    name = raw_line
    quantity = 50.0
    unit = "g"

    # Pattern 1: Parentheses with quantity: e.g. 'cá mối (500g)'
    paren_match = re.search(
        r"\(([\d\.,/]+)\s*" + UNIT_PATTERN + r"\)", name, re.IGNORECASE
    )
    if paren_match:
        qty_str = paren_match.group(1).replace(",", ".")
        try:
            quantity = float(qty_str)
        except ValueError:
            quantity = 100.0
        unit = paren_match.group(2).lower()
        name = re.sub(
            r"\s*\([\d\.,/]+\s*" + UNIT_PATTERN + r"\)", "", name, flags=re.IGNORECASE
        ).strip()

    # Pattern 2: Leading quantity: e.g. '300g đầu mực', '100g nấm'
    elif re.match(r"^([\d\.,/]+)\s*" + UNIT_PATTERN + r"\s+(.*)$", name, re.IGNORECASE):
        lead_match = re.match(
            r"^([\d\.,/]+)\s*" + UNIT_PATTERN + r"\s+(.*)$", name, re.IGNORECASE
        )
        if lead_match:
            qty_str = lead_match.group(1).replace(",", ".")
            try:
                quantity = float(qty_str)
            except ValueError:
                quantity = 100.0
            unit = lead_match.group(2).lower()
            name = lead_match.group(3).strip()

    # Pattern 3: Colon separation: e.g. 'Phi lê cá điêu hồng: 300g'
    elif ":" in name:
        name_part, qty_part = name.split(":", 1)
        name_part = name_part.strip()
        qty_part = qty_part.strip()

        m = re.match(r"^([\d\.,/]+)\s*(.*)$", qty_part)
        if m:
            qty_str = m.group(1).replace(",", ".")
            try:
                if "/" in qty_str:
                    num, denom = qty_str.split("/", 1)
                    quantity = float(num) / float(denom)
                else:
                    quantity = float(qty_str)
            except (ValueError, ZeroDivisionError):
                quantity = 50.0
            unit = m.group(2).strip().lower() or "g"
            name = name_part
        elif "gia vị" in name_part.lower():
            name = "Gia vị tổng hợp"
            quantity = 10.0
            unit = "g"
        else:
            name = name_part
            quantity = 30.0
            unit = "g"

    # Normalize units to standard weights/volumes accepted by DB
    unit_map = {
        "gram": "g",
        "grams": "g",
        "kilogram": "kg",
        "kilograms": "kg",
        "liter": "l",
        "liters": "l",
        "milliliter": "ml",
        "milliliters": "ml",
    }
    # Count and spoon units ("gói", "quả", "tép", "muỗng canh") stay as written;
    # relabelling them as grams turned "1 gói mì" into "1 g" on grocery lists.
    unit = unit_map.get(unit, unit)

    # Category determination for check constraint ('protein', 'produce', 'pantry')
    lower = name.lower()
    if any(re.search(rf"\b{re.escape(k)}\b", lower) for k in PROTEIN_KEYWORDS):
        category = "protein"
    elif any(re.search(rf"\b{re.escape(k)}\b", lower) for k in PRODUCE_KEYWORDS):
        category = "produce"
    else:
        category = "pantry"

    return {
        "is_equipment": False,
        "name": name,
        "quantity": quantity,
        "unit": unit,
        "category": category,
    }


def transform_raw_recipe(
    raw: dict[str, Any],
    key_counter: Counter[str],
    default_cuisine: str = "vietnamese",
) -> dict[str, Any] | None:
    """Transform raw crawled JSON into the catalog recipe manifest format."""
    recipe_name = (raw.get("recipe_name") or raw.get("name") or "").strip()
    if not recipe_name:
        return None

    recipe_tag = raw.get("tag")
    if _is_non_meal_title(recipe_name, recipe_tag):
        inferred_types = infer_meal_types(recipe_name)
        if inferred_types != ["snack"]:
            return None

    raw_ingredients = raw.get("ingredients") or []
    raw_instructions = raw.get("instructions") or []

    # Skip recipes that lack ingredients or cooking steps
    if not raw_ingredients or not raw_instructions:
        return None

    base_slug = slugify(recipe_name)
    key_counter[base_slug] += 1
    count = key_counter[base_slug]
    recipe_key = base_slug if count == 1 else f"{base_slug}-{count}"

    # 1. Parse ingredients and extract equipment
    equipment_items: list[str] = []
    clean_ingredients: list[dict[str, Any]] = []

    for raw_ing in raw_ingredients:
        parsed = parse_ingredient_line(str(raw_ing))
        if parsed.get("is_equipment"):
            equipment_items.append(parsed["text"])
        else:
            clean_ingredients.append(
                {
                    "name": parsed["name"],
                    "quantity": parsed["quantity"],
                    "unit": parsed["unit"],
                    "category": parsed["category"],
                }
            )

    if not clean_ingredients:
        return None

    # 2. Parse instruction steps (enforce consecutive numbering)
    clean_steps: list[dict[str, Any]] = []
    for idx, raw_step in enumerate(raw_instructions, start=1):
        parsed_step = parse_instruction_step(str(raw_step), idx)
        parsed_step["step_number"] = idx
        clean_steps.append(parsed_step)

    # 3. Format allergens
    raw_allergens = raw.get("allergens")
    if isinstance(raw_allergens, list):
        allergens_str = ", ".join(str(a) for a in raw_allergens)
    elif isinstance(raw_allergens, str):
        allergens_str = raw_allergens.strip()
    else:
        allergens_str = None

    # 4. Meal types
    meal_types = raw.get("meal_types") or infer_meal_types(recipe_name)
    if _is_non_meal_title(recipe_name, recipe_tag):
        meal_types = ["snack"]

    return {
        "recipe_key": recipe_key,
        "name": recipe_name,
        "cuisine": raw.get("cuisine", default_cuisine),
        "meal_types": meal_types,
        "popularity_rank": raw.get("popularity_rank", count),
        "description": None,  # Dropping scraped marketing copy
        "equipment": ", ".join(equipment_items) if equipment_items else None,
        "allergens": allergens_str,
        "prep_time_minutes": raw.get("prep_time_minutes", 15),
        "cook_time_minutes": raw.get("cook_time_minutes", 15),
        "ingredients": clean_ingredients,
        "steps": clean_steps,
    }


def _is_non_meal_title(title: str, tag: Any = None) -> bool:
    from src.domain.services.weekly_meal_planner.weekly_plan_generation_service import (
        is_non_meal_title,
    )

    return is_non_meal_title(title, str(tag) if tag else None)


def read_structured_json_from_excel_or_csv(
    file_path: Path, column_name: str = "Structured JSON"
) -> list[dict[str, Any]]:
    """Read rows and extract the target JSON column from any matching sheet."""
    recipes: list[dict[str, Any]] = []
    suffix = file_path.suffix.lower()

    if suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(file_path, data_only=True)
        target_sheet = None
        target_col_idx = None

        for sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
            first_row = next(sheet.iter_rows(max_row=1, values_only=True), None)
            if not first_row:
                continue
            headers = [
                str(c).strip().lower() if c is not None else "" for c in first_row
            ]
            for idx, h in enumerate(headers):
                if h == column_name.lower():
                    target_sheet = sheet
                    target_col_idx = idx
                    print(
                        f"Found column '{column_name}' in sheet '{sheet_name}' (column index {idx})"
                    )
                    break
            if target_sheet is not None:
                break

        if target_sheet is None or target_col_idx is None:
            raise ValueError(
                f"Column '{column_name}' not found across any sheet in {file_path.name}"
            )

        for row in target_sheet.iter_rows(min_row=2, values_only=True):
            val = row[target_col_idx]
            if not val:
                continue
            try:
                recipes.append(json.loads(str(val)))
            except Exception as e:
                print(f"Skipping row due to JSON parse error: {e}")

    elif suffix == ".csv":
        with file_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            target_col = None
            for key in reader.fieldnames or []:
                if key.strip().lower() == column_name.lower():
                    target_col = key
                    break

            if not target_col:
                raise ValueError(
                    f"Column '{column_name}' not found in CSV headers: {reader.fieldnames}"
                )

            for row in reader:
                val = row.get(target_col)
                if not val:
                    continue
                try:
                    recipes.append(json.loads(val))
                except Exception as e:
                    print(f"Skipping row due to JSON parse error: {e}")
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Supported: .xlsx, .csv")

    return recipes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert Excel/CSV recipes with Structured JSON column to catalog manifest."
    )
    parser.add_argument("--file", required=True, help="Path to .xlsx or .csv file")
    parser.add_argument(
        "--column",
        default="Structured JSON",
        help="Name of column with JSON (default: 'Structured JSON')",
    )
    parser.add_argument(
        "--output",
        default="scripts/data/imported-excel-recipes.json",
        help="Output manifest path",
    )
    parser.add_argument(
        "--cuisine",
        default="vietnamese",
        help="Default cuisine (default: 'vietnamese')",
    )
    args = parser.parse_args()

    input_path = Path(args.file)
    if not input_path.exists():
        raise FileNotFoundError(f"File not found: {input_path}")

    raw_items = read_structured_json_from_excel_or_csv(
        input_path, column_name=args.column
    )
    print(f"Loaded {len(raw_items)} raw JSON records from {input_path}")

    key_counter: Counter[str] = Counter()
    transformed = []
    skipped_incomplete = 0

    for item in raw_items:
        res = transform_raw_recipe(
            item, key_counter=key_counter, default_cuisine=args.cuisine
        )
        if res is not None:
            transformed.append(res)
        else:
            skipped_incomplete += 1

    manifest = {
        "release_key": f"excel-import-{input_path.stem}",
        "expected_recipe_count": len(transformed),
        "recipes": transformed,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"Valid recipes transformed: {len(transformed)}")
    print(f"Incomplete rows skipped (no ingredients/steps): {skipped_incomplete}")
    print(f"Successfully generated manifest at: {output_path}")
    print(
        "\nNext step: Run dry-run seed import to resolve ingredients against food_reference:"
    )
    print(
        f"  uv run python scripts/import_catalog_recipe_seeds.py --manifest {output_path} --partial --resolve-all-best-effort --dry-run"
    )


if __name__ == "__main__":
    main()
