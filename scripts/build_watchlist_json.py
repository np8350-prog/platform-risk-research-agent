"""
Builds data/watchlist/processed/{vendor}.json from the raw .txt files in
data/watchlist/raw/{vendor}/.

Output shape matches the vocabulary in agent/state.py (PatternResult.confidence
and PLATFORM_RISK_DIMENSIONS), so the watchlist-check node can drop this
straight into a PatternResult without a translation step later.

Each vendor folder must contain:
  stability.txt, incidents.txt, community.txt,
  compliance.txt, data_handling.txt, integration.txt, meta.txt

meta.txt format (one key: value per line):
  confidence_<dimension>: strong|limited|inferred|none
  source_type_<dimension>: free text
  last_updated: YYYY-MM-DD

Run from the project root:
  python scripts/build_watchlist_json.py
"""

import json
import os

RAW_DIR = "data/watchlist/raw"
PROCESSED_DIR = "data/watchlist/processed"

# Maps internal file names to the exact dimension names in
# agent.state.PLATFORM_RISK_DIMENSIONS. Order matters there; this dict
# just needs the right strings, since we key by dimension name below.
DIMENSION_LABELS = {
    "stability": "Vendor Stability",
    "incidents": "Incident History",
    "community": "Community Signal",
    "compliance": "Compliance Posture",
    "data_handling": "Data Handling Posture",
    "integration": "Integration Risk",
}

# meta.txt uses short labels (strong/limited/inferred/none). state.py's
# PatternResult.confidence expects these exact strings instead.
CONFIDENCE_MAP = {
    "strong": "strong evidence",
    "limited": "limited evidence",
    "inferred": "inferred",
    "none": "no signal found",
}


def read_file(path):
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def parse_meta(path):
    """Parses meta.txt into a dict of dimension -> {confidence, source_type} plus last_updated."""
    meta = {"last_updated": None, "dimensions": {}}
    if not os.path.exists(path):
        return meta

    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    for line in lines:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        if key == "last_updated":
            meta["last_updated"] = value
        elif key.startswith("confidence_"):
            dim = key[len("confidence_"):]
            meta["dimensions"].setdefault(dim, {})
            raw_conf = value.lower()
            if raw_conf not in CONFIDENCE_MAP:
                print(f"  WARNING: unrecognized confidence value '{value}' for '{dim}' "
                      f"(expected one of {sorted(CONFIDENCE_MAP.keys())}) — defaulting to 'no signal found'")
            meta["dimensions"][dim]["confidence"] = CONFIDENCE_MAP.get(raw_conf, "no signal found")
        elif key.startswith("source_type_"):
            dim = key[len("source_type_"):]
            meta["dimensions"].setdefault(dim, {})
            meta["dimensions"][dim]["source_type"] = value

    return meta


def build_vendor_json(vendor_name, vendor_dir):
    meta = parse_meta(os.path.join(vendor_dir, "meta.txt"))
    dimensions = {}

    for file_key, dimension_name in DIMENSION_LABELS.items():
        content = read_file(os.path.join(vendor_dir, f"{file_key}.txt"))
        meta_entry = meta["dimensions"].get(file_key, {})

        dimensions[dimension_name] = {
            "content": content,
            "confidence": meta_entry.get("confidence", "no signal found"),
            "source_type": meta_entry.get("source_type", ""),
        }

        if not content:
            print(f"  WARNING: {vendor_name}/{file_key}.txt is empty")
        if file_key not in meta["dimensions"]:
            print(f"  WARNING: {vendor_name} has no confidence tag for '{file_key}' in meta.txt")

    return {
        "vendor": vendor_name,
        "last_updated": meta["last_updated"],
        "dimensions": dimensions,
    }


def main():
    if not os.path.isdir(RAW_DIR):
        raise SystemExit(f"Can't find {RAW_DIR}. Run this from the project root.")

    os.makedirs(PROCESSED_DIR, exist_ok=True)

    vendors = sorted(
        d for d in os.listdir(RAW_DIR)
        if os.path.isdir(os.path.join(RAW_DIR, d))
    )

    if not vendors:
        raise SystemExit(f"No vendor folders found in {RAW_DIR}.")

    built = 0
    for vendor in vendors:
        vendor_dir = os.path.join(RAW_DIR, vendor)
        print(f"Processing {vendor}...")
        data = build_vendor_json(vendor, vendor_dir)

        out_path = os.path.join(PROCESSED_DIR, f"{vendor}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        built += 1

    print(f"\nDone. Built {built} vendor JSON files in {PROCESSED_DIR}/")


if __name__ == "__main__":
    main()