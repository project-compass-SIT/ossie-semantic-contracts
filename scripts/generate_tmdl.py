"""
generate_tmdl.py

Step 7 (Power BI side): turns OSSIE YAML into one TMDL file per table.

Your team already has this converter (referenced as ossie_to_tmdl.py, with
-s/-o/-m flags). Drop that script's actual logic in here, or import it — this
file is a thin, pipeline-friendly wrapper so `deploy.yml` has a single stable
entry point regardless of how the converter itself evolves.

Usage:
    python generate_tmdl.py --input ossie/ --output build/tmdl/ --multi-file

Replace the body of `convert()` with a call to your existing converter, e.g.:
    from ossie_to_tmdl import convert_directory
    convert_directory(input_dir, output_dir, multi_file=multi_file)
"""
import argparse
import sys
from pathlib import Path


def convert(input_dir: Path, output_dir: Path, multi_file: bool) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Plug in your existing converter here ---------------------------
    # This is a placeholder so the pipeline scaffold is runnable end-to-end
    # before you wire in the real logic. Replace it with the actual call to
    # your team's ossie_to_tmdl converter.
    raise NotImplementedError(
        "Wire this up to your team's existing ossie_to_tmdl.py converter "
        "(the one with -s/-o/-m flags). This wrapper exists so deploy.yml "
        "doesn't need to know converter internals."
    )
    # ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Directory of OSSIE YAML files")
    parser.add_argument("--output", required=True, help="Directory to write TMDL files")
    parser.add_argument("--multi-file", action="store_true", help="One TMDL file per table")
    args = parser.parse_args()

    try:
        convert(Path(args.input), Path(args.output), args.multi_file)
    except NotImplementedError as e:
        print(f"[SKIPPED] {e}")
        # Exit 0 for now so the rest of the pipeline (Snowflake deploy) can
        # still be exercised while the TMDL converter is being wired in.
        # Remove this once convert() is implemented, so a real failure fails
        # the build.
        sys.exit(0)


if __name__ == "__main__":
    main()
