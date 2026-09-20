"""
generate_tmdl.py

Step 7 (Power BI side): turns each OSSIE YAML file under --input into a set
of TMDL files under --output (one subfolder per model, one .tmdl per table
plus ALL_MEASURES.tmdl, relationships.tmdl and a combined model.tmdl —
that's what OSSIEToTMDLConverter.save_multipart() in convertor.py produces).

This is a thin CLI wrapper around your team's convertor.py — it doesn't
change any of its conversion logic, it just:
  - loops over every OSSIE YAML file in the input directory (convertor.py
    itself only handles one file/one semantic model at a time), and
  - replaces convertor.py's hardcoded INPUT_YAML_PATH/OUTPUT_DIR_PATH
    globals with real CLI arguments, so it's pipeline-friendly.

Usage:
    python generate_tmdl.py --input ossie/ --output build/tmdl/ --multi-file
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from convertor import OSSIEToTMDLConverter  # noqa: E402


def convert(input_dir: Path, output_dir: Path, multi_file: bool) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    yaml_files = sorted(input_dir.glob("*.yml")) + sorted(input_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"No OSSIE YAML files found in {input_dir}")
        return []

    written: list[str] = []
    for yf in yaml_files:
        # One subfolder per source file, named after the file itself, so
        # multiple OSSIE YAML files never overwrite each other's TMDL output.
        model_out_dir = output_dir / yf.stem
        converter = OSSIEToTMDLConverter(str(yf), str(model_out_dir))

        if multi_file:
            saved = converter.save_multipart()
        else:
            converter.load_yaml()
            converter.validate_model()
            model_out_dir.mkdir(parents=True, exist_ok=True)
            out_path = model_out_dir / "model.tmdl"
            out_path.write_text(converter.convert(), encoding="utf-8")
            saved = [str(out_path)]

        print(f"[OK] {yf.name} -> {model_out_dir} ({len(saved)} file(s))")
        written.extend(saved)

    return written


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Directory of OSSIE YAML files")
    parser.add_argument("--output", required=True, help="Directory to write TMDL files")
    parser.add_argument("--multi-file", action="store_true", help="One TMDL file per table (plus ALL_MEASURES/relationships/model.tmdl)")
    args = parser.parse_args()

    written = convert(Path(args.input), Path(args.output), args.multi_file)
    if not written:
        sys.exit(1)


if __name__ == "__main__":
    main()
