import argparse
import warnings
from pathlib import Path

import yaml
from ossie_microsoft import convert_ossie_to_semantic_model


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_yaml", type=Path)
    parser.add_argument("output_tmdl", type=Path)
    args = parser.parse_args()

    model = yaml.safe_load(args.input_yaml.read_text(encoding="utf-8-sig"))

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        tmdl = convert_ossie_to_semantic_model(
            model,
            output_format="TMDL",
        )

    args.output_tmdl.parent.mkdir(parents=True, exist_ok=True)
    args.output_tmdl.write_text(tmdl, encoding="utf-8")


if __name__ == "__main__":
    main()