"""
OSSIE YAML to Power BI TMDL Converter
Converts OSSIE semantic model YAML (the format used in sample_ossie_model.yml)
into Power BI TMDL files.

Run:
    python convertor.py
"""

import yaml
import os
import re
import uuid
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ======================================================================
# CONFIG  -- EDIT THESE TWO PATHS
# ======================================================================
INPUT_YAML_PATH = r"C:\Users\PranavSharma\OneDrive - Snap Analytics\Desktop\OSSIE CI\testing\sample_ossie_model.yml"
OUTPUT_DIR_PATH = r"C:\Users\PranavSharma\OneDrive - Snap Analytics\Desktop\OSSIE CI\testing\convertor_results"

# True  -> one .tmdl per component, all in the same folder
# False -> only a single combined model.tmdl
MULTIPART = True
# ======================================================================


class OSSIEToTMDLConverter:
    """
    Converts OSSIE semantic-model YAML (with the schema used by
    sample_ossie_model.yml) into Power BI TMDL files.
    """

    def __init__(self, yaml_file_path: str, output_dir: str = "tmdl_output"):
        self.yaml_file_path = yaml_file_path
        self.output_dir = Path(output_dir)
        self.raw: Dict[str, Any] = {}
        self.model: Dict[str, Any] = {}          # semantic_model[0]
        self._lineage_cache: Dict[str, str] = {}

        # Flat output: no subfolders
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _new_lineage_tag(seed: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, seed))

    def _lineage(self, key: str) -> str:
        if key not in self._lineage_cache:
            self._lineage_cache[key] = self._new_lineage_tag(key)
        return self._lineage_cache[key]

    @staticmethod
    def _t(level: int) -> str:
        return "\t" * level

    @staticmethod
    def _safe_filename(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.\-]+", "_", name).strip("_") or "unnamed"

    @staticmethod
    def _first_dialect_expression(node: Any) -> str:
        if not isinstance(node, dict):
            return ""
        dialects = node.get("dialects") or []
        if not dialects:
            return ""
        for d in dialects:
            if (d.get("dialect") or "").upper() == "SNOWFLAKE":
                return (d.get("expression") or "").strip()
        return (dialects[0].get("expression") or "").strip()

    # ------------------------------------------------------------------
    # Load / validate
    # ------------------------------------------------------------------
    def load_yaml(self) -> Dict:
        try:
            with open(self.yaml_file_path, "r", encoding="utf-8") as f:
                self.raw = yaml.safe_load(f) or {}
            logger.info(f"Loaded YAML from {self.yaml_file_path}")
        except FileNotFoundError:
            logger.error(f"YAML file not found: {self.yaml_file_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML: {e}")
            raise

        sm = self.raw.get("semantic_model") or []
        if isinstance(sm, list):
            self.model = sm[0] if sm else {}
        elif isinstance(sm, dict):
            self.model = sm
        else:
            self.model = {}

        return self.raw

    def validate_model(self) -> bool:
        if not self.model:
            raise ValueError(
                "YAML does not contain a 'semantic_model' section"
            )
        self.model.setdefault("datasets", [])
        self.model.setdefault("relationships", [])
        self.model.setdefault("metrics", [])
        return True

    # ------------------------------------------------------------------
    # Source parsing  (SIT.COMPASS.CUSTOMER -> database/schema/table)
    # ------------------------------------------------------------------
    def _parse_source(self, source_str: str) -> Dict[str, str]:
        parts = (source_str or "").split(".")
        if len(parts) == 3:
            return {"database": parts[0], "schema": parts[1], "table": parts[2]}
        if len(parts) == 2:
            return {"database": "", "schema": parts[0], "table": parts[1]}
        if len(parts) == 1:
            return {"database": "", "schema": "", "table": parts[0]}
        return {"database": "", "schema": "", "table": ""}

    # ------------------------------------------------------------------
    # Type inference
    # ------------------------------------------------------------------
    def _map_data_type(self, expression: str, field_name: str) -> str:
        n = (field_name or "").upper()
        if n.endswith("DATE") or n.endswith("_DT") or "DATE" in n:
            return "dateTime"
        if n.endswith("KEY") or n.endswith("KEY_"):
            return "int64"
        if "QUANTITY" in n or n.endswith("QTY"):
            return "int64"
        if "PRICE" in n or "AMOUNT" in n or n.endswith("BAL"):
            return "double"
        if "NUMBER" in n:
            return "int64"
        return "string"

    def _default_summarize_by(self, pbi_type: str, is_key: bool) -> str:
        if is_key:
            return "none"
        if pbi_type in ("int64", "double", "decimal"):
            return "sum"
        return "none"

    # ------------------------------------------------------------------
    # Column
    # ------------------------------------------------------------------
    def _generate_column_tmdl(self, dataset_name: str, field: Dict,
                              is_key: bool, level: int = 1) -> List[str]:
        t = self._t(level)
        lines: List[str] = []

        col_name = (field.get("name") or "").strip()
        if not col_name:
            logger.warning(f"Field missing name in dataset '{dataset_name}'")
            return lines

        expression = self._first_dialect_expression(field.get("expression"))
        source_col = expression or col_name
        pbi_type = self._map_data_type(expression, col_name)
        summarize = self._default_summarize_by(pbi_type, is_key)

        dim = field.get("dimension") or {}
        if isinstance(dim, dict) and dim.get("is_time"):
            pbi_type = "dateTime"

        lines.append(f"{t}column {col_name}")
        lines.append(f"{t}\tdataType: {pbi_type}")
        lines.append(
            f"{t}\tlineageTag: {self._lineage(f'{dataset_name}.{col_name}')}"
        )
        lines.append(f"{t}\tsummarizeBy: {summarize}")
        lines.append(f"{t}\tsourceColumn: {source_col}")

        if is_key:
            lines.append(f"{t}\tisKey")

        lines.append("")
        lines.append(f"{t}\tannotation SummarizationSetBy = Automatic")

        if pbi_type in ("int64", "double", "decimal"):
            lines.append("")
            lines.append(
                f'{t}\tannotation PBI_FormatHint = {{"isGeneralNumber":true}}'
            )

        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Partition (Power Query M)
    # ------------------------------------------------------------------
    def _build_m_source(self, dataset: Dict) -> List[str]:
        src = self._parse_source(dataset.get("source", ""))
        database = src["database"] or "YOUR_DB"
        schema = src["schema"] or "PUBLIC"
        table = src["table"] or dataset.get("name", "TABLE")
        safe_table = re.sub(r"\W+", "_", table)

        server = "YOUR_SERVER.snowflakecomputing.com"
        warehouse = "COMPUTE_WH"

        return [
            "let",
            f'    Source = Snowflake.Databases("{server}","{warehouse}",'
            f'[Implementation="2.0"]),',
            f'    {database}_Database = Source{{[Name="{database}",'
            f'Kind="Database"]}}[Data],',
            f'    {schema}_Schema = {database}_Database{{[Name="{schema}",'
            f'Kind="Schema"]}}[Data],',
            f'    {safe_table}_Table = {schema}_Schema{{[Name="{table}",'
            f'Kind="Table"]}}[Data]',
            "in",
            f"    {safe_table}_Table",
        ]

    def _generate_partition_tmdl(self, dataset: Dict,
                                 level: int = 1) -> List[str]:
        t = self._t(level)
        name = dataset["name"]
        mode = (dataset.get("partition_mode") or "import").lower()
        m_lines = self._build_m_source(dataset)

        lines = [
            f"{t}partition {name} = m",
            f"{t}\tmode: {mode}",
            f"{t}\tsource =",
        ]
        for ln in m_lines:
            lines.append(f"{t}\t\t{ln}" if ln else "")
        lines.append("")
        return lines

    # ------------------------------------------------------------------
    # Metrics / measures
    # ------------------------------------------------------------------
    def _expression_to_dax(self, expr: str) -> str:
        if not expr:
            return ""
        expr = expr.strip()

        m = re.match(r"^\s*([A-Za-z_][\w]*)\s*/\s*([A-Za-z_][\w]*)\s*$", expr)
        if m:
            return f"DIVIDE([{m.group(1)}], [{m.group(2)}])"

        m = re.match(
            r"(?i)^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*([\w.]+)\s*\)\s*$",
            expr,
        )
        if m:
            func = m.group(1).upper()
            ref = m.group(2)
            if "." in ref:
                tbl, col = ref.split(".", 1)
                return f"{func}({tbl.upper()}[{col.upper()}])"
            return f"{func}([{ref.upper()}])"

        if re.match(r"^[A-Za-z_][\w]*$", expr):
            return f"[{expr}]"

        return expr

    def _generate_measure_tmdl(self, metric: Dict,
                               level: int = 1) -> List[str]:
        t = self._t(level)
        name = (metric.get("name") or "").strip()
        if not name:
            return []

        raw_expr = self._first_dialect_expression(metric.get("expression"))
        dax = self._expression_to_dax(raw_expr)
        if not dax:
            logger.warning(f"Metric '{name}' has no usable expression")
            return []

        lines = [f"{t}measure {name} = {dax}"]
        desc = metric.get("description")
        if desc:
            lines.append(f'{t}\tdescription: "{desc}"')
        lines.append(f"{t}\tformatString: 0.00")
        lines.append(f"{t}\tlineageTag: {self._lineage(f'measure.{name}')}")
        return lines

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    def _generate_relationship_block(self, rel: Dict) -> str:
        name = rel.get("name") or ""
        from_table = rel.get("from")
        to_table = rel.get("to")
        from_cols = rel.get("from_columns") or []
        to_cols = rel.get("to_columns") or []

        if not (from_table and to_table and from_cols and to_cols):
            logger.warning(f"Skipping malformed relationship: {rel}")
            return ""

        from_col = from_cols[0]
        to_col = to_cols[0]

        rel_id = name or str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{from_table}.{from_col}->{to_table}.{to_col}"
        ))

        lines = [
            f"relationship {rel_id}",
            f"\tfromColumn: {from_table}.{from_col}",
            f"\ttoColumn: {to_table}.{to_col}",
        ]

        card = rel.get("cardinality")
        if card:
            card_map = {
                "many_to_one": "manyToOne",
                "one_to_many": "oneToMany",
                "one_to_one": "oneToOne",
                "many_to_many": "manyToMany",
            }
            lines.append(f"\tcardinality: {card_map.get(card, card)}")

        cf = rel.get("cross_filter")
        if cf:
            cf_map = {
                "single_direction": "singleDirection",
                "both_directions": "bothDirections",
            }
            lines.append(f"\tcrossFilteringBehavior: {cf_map.get(cf, cf)}")

        if rel.get("is_active") is False:
            lines.append("\tisActive: false")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def generate_tmdl_table(self, dataset: Dict) -> str:
        name = (dataset.get("name") or "").strip()
        if not name:
            raise ValueError("Dataset missing name attribute")

        primary_keys = set(dataset.get("primary_key") or [])
        fields = dataset.get("fields") or []

        lines: List[str] = []
        lines.append(f"table {name}")
        lines.append(f"\tlineageTag: {self._lineage(f'table.{name}')}")
        lines.append("")

        for field in fields:
            fname = field.get("name")
            col_lines = self._generate_column_tmdl(
                name, field, is_key=(fname in primary_keys), level=1
            )
            if col_lines:
                lines.extend(col_lines)

        dataset_metrics = [
            m for m in self.model.get("metrics", [])
            if m.get("dataset") == name
        ]
        for m in dataset_metrics:
            lines.extend(self._generate_measure_tmdl(m, level=1))
            lines.append("")

        lines.extend(self._generate_partition_tmdl(dataset, level=1))

        lines.append("\tannotation PBI_ResultType = Table")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # ALL_MEASURES
    # ------------------------------------------------------------------
    def generate_all_measures_table(self) -> str:
        metrics = self.model.get("metrics") or []
        if not metrics:
            return ""

        lines: List[str] = []
        lines.append("table ALL_MEASURES")
        lines.append(f"\tlineageTag: {self._lineage('table.ALL_MEASURES')}")
        lines.append("")

        for m in metrics:
            m_lines = self._generate_measure_tmdl(m, level=1)
            if m_lines:
                lines.extend(m_lines)
                lines.append("")

        lines.append("partition ALL_MEASURES = m")
        lines.append("\tmode: import")
        lines.append("\tsource =")
        lines.append("\t\tlet")
        lines.append(
            "\t\t    Source = Table.FromRows(Json.Document(Binary.Decompress("
            'Binary.FromText("i44FAA==", BinaryEncoding.Base64), '
            "Compression.Deflate)), let _t = ((type nullable text) "
            "meta [Serialized.Text = true]) in type table [Column1 = _t]),"
        )
        lines.append(
            '\t\t    #"Removed Columns" = Table.RemoveColumns(Source,{"Column1"})'
        )
        lines.append("\t\tin")
        lines.append('\t\t    #"Removed Columns"')
        lines.append("")
        lines.append("\tannotation PBI_NavigationStepName = Navigation")
        lines.append("")
        lines.append("\tannotation PBI_ResultType = Table")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Full model
    # ------------------------------------------------------------------
    def convert(self) -> str:
        self.load_yaml()
        self.validate_model()

        parts: List[str] = []

        for dataset in self.model.get("datasets", []):
            parts.append(self.generate_tmdl_table(dataset))
            parts.append("")

        am = self.generate_all_measures_table()
        if am:
            parts.append(am)
            parts.append("")

        for rel in self.model.get("relationships", []):
            block = self._generate_relationship_block(rel)
            if block:
                parts.append(block)
                parts.append("")

        return "\n".join(parts).rstrip() + "\n"

    # ------------------------------------------------------------------
    # Saving  (everything flat, all .tmdl)
    # ------------------------------------------------------------------
    def _write(self, path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"Wrote {path}")
        return str(path)

    def save_multipart(self) -> List[str]:
        self.load_yaml()
        self.validate_model()
        saved: List[str] = []

        # 1) One .tmdl per dataset — placed directly in output_dir
        for dataset in self.model.get("datasets", []):
            name = dataset["name"]
            path = self.output_dir / f"{self._safe_filename(name)}.tmdl"
            saved.append(self._write(path, self.generate_tmdl_table(dataset)))

        # 2) ALL_MEASURES.tmdl
        if self.model.get("metrics"):
            saved.append(self._write(
                self.output_dir / "ALL_MEASURES.tmdl",
                self.generate_all_measures_table() + "\n",
            ))

        # 3) relationships.tmdl
        rel_blocks = [
            self._generate_relationship_block(r)
            for r in self.model.get("relationships", [])
        ]
        rel_blocks = [b for b in rel_blocks if b]
        if rel_blocks:
            saved.append(self._write(
                self.output_dir / "relationships.tmdl",
                "\n\n".join(rel_blocks) + "\n",
            ))

        # 4) model.tmdl
        saved.append(self._write(
            self.output_dir / "model.tmdl", self.convert()
        ))

        logger.info(f"Saved {len(saved)} .tmdl files under {self.output_dir}")
        return saved


def main():
    input_path = INPUT_YAML_PATH
    output_path = OUTPUT_DIR_PATH

    if not os.path.exists(input_path):
        logger.error(f"Input file not found: {input_path}")
        return

    converter = OSSIEToTMDLConverter(input_path, output_path)

    if MULTIPART:
        converter.save_multipart()
    else:
        converter.load_yaml()
        converter.validate_model()
        Path(output_path).mkdir(parents=True, exist_ok=True)
        out = Path(output_path) / "model.tmdl"
        out.write_text(converter.convert(), encoding="utf-8")
        logger.info(f"Wrote {out}")

    logger.info("Conversion completed successfully!")


if __name__ == "__main__":
    main()