# Copyright 2026 Joe Harris / BenchBox Project

# TPC Benchmark™ DI (TPC-DI) - Copyright © Transaction Processing Performance Council
# This implementation is based on the TPC-DI specification.

# Licensed under the MIT License. See LICENSE file in the project root for details.

from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from benchbox.core.tpcdi.scaling import ROWS_PER_SCALE_FACTOR
from benchbox.core.tpcdi.source_generators import TPCDISourceDataGenerator


class SourceDataFormat(ABC):
    _generator: Optional[TPCDISourceDataGenerator]

    @abstractmethod
    def generate_data(self, table_name: str, record_count: int, **kwargs: Any) -> Any: ...

    def _run_generator(
        self,
        gen_method_name: str,
        record_count: int,
        default_scale_divisor: float,
        **kwargs: Any,
    ) -> str:
        scale_factor = kwargs.get("scale_factor", record_count / default_scale_divisor)
        output_dir = kwargs.get("output_dir", Path.cwd() / "tpcdi_source_data")

        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=scale_factor,
                output_dir=output_dir,
            )

        files = getattr(self._generator, gen_method_name)()
        return files[0] if files else ""


class CSVSourceFormat(SourceDataFormat):
    def __init__(self, delimiter: str = ",", quote_char: str = '"', encoding: str = "utf-8") -> None:
        self.delimiter = delimiter
        self.quote_char = quote_char
        self.encoding = encoding
        self._generator: Optional[TPCDISourceDataGenerator] = None

    def generate_data(self, table_name: str, record_count: int, **kwargs: Any) -> str:
        return self._run_generator("_generate_oltp_data", record_count, ROWS_PER_SCALE_FACTOR["customers"], **kwargs)

    def write_to_file(self, data: pd.DataFrame, file_path: Path) -> None:
        data.to_csv(
            file_path,
            sep=self.delimiter,
            quotechar=self.quote_char,
            encoding=self.encoding,
            index=False,
        )


class XMLSourceFormat(SourceDataFormat):
    def __init__(self, root_element: str = "data", record_element: str = "record") -> None:
        self.root_element = root_element
        self.record_element = record_element
        self._generator: Optional[TPCDISourceDataGenerator] = None

    def generate_data(self, table_name: str, record_count: int, **kwargs: Any) -> str:
        return self._run_generator("_generate_hr_data", record_count, 500, **kwargs)

    def write_to_file(self, data: pd.DataFrame, file_path: Path) -> None:
        data.to_xml(
            file_path,
            index=False,
            root_name=self.root_element,
            row_name=self.record_element,
        )


class FixedWidthSourceFormat(SourceDataFormat):
    def __init__(self, field_widths: dict[str, int], fill_char: str = " ") -> None:
        self.field_widths = field_widths
        self.fill_char = fill_char
        self._generator: Optional[TPCDISourceDataGenerator] = None

    def generate_data(self, table_name: str, record_count: int, **kwargs: Any) -> str:
        return self._run_generator(
            "_generate_external_data", record_count, ROWS_PER_SCALE_FACTOR["customers"], **kwargs
        )

    def write_to_file(self, data: pd.DataFrame, file_path: Path) -> None:
        with open(file_path, "w", encoding="utf-8") as f:
            for _, row in data.iterrows():
                line = ""
                for col in data.columns:
                    width = self.field_widths.get(col, 20)
                    value = str(row[col])[:width]
                    line += value.ljust(width, self.fill_char)
                f.write(line + "\n")


class PipeDelimitedSourceFormat(SourceDataFormat):
    def __init__(self, escape_char: str = "\\", null_representation: str = "") -> None:
        self.delimiter = "|"
        self.escape_char = escape_char
        self.null_representation = null_representation
        self._generator: Optional[TPCDISourceDataGenerator] = None

    def generate_data(self, table_name: str, record_count: int, **kwargs: Any) -> str:
        scale_factor = kwargs.get("scale_factor", record_count / ROWS_PER_SCALE_FACTOR["customers"])
        output_dir = kwargs.get("output_dir", Path.cwd() / "tpcdi_source_data")

        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=scale_factor,
                output_dir=output_dir,
            )

        all_files = self._generator.generate_all_source_data()

        oltp_files = all_files.get("oltp", [])
        return oltp_files[0] if oltp_files else ""

    def write_to_file(self, data: pd.DataFrame, file_path: Path) -> None:
        data.to_csv(
            file_path,
            sep=self.delimiter,
            index=False,
            na_rep=self.null_representation,
        )


class SourceDataGenerator:
    def __init__(self, scale_factor: float = 1.0, seed: Optional[int] = None) -> None:
        self.scale_factor = scale_factor
        self.seed = seed
        self.formats: dict[str, SourceDataFormat] = {}
        self._generator: Optional[TPCDISourceDataGenerator] = None
        self._register_default_formats()

    def _register_default_formats(self) -> None:
        self.formats["csv"] = CSVSourceFormat()
        self.formats["xml"] = XMLSourceFormat()
        self.formats["fixed_width"] = FixedWidthSourceFormat(field_widths={})
        self.formats["pipe"] = PipeDelimitedSourceFormat()

    def register_format(self, name: str, format_instance: SourceDataFormat) -> None:
        self.formats[name] = format_instance

    def generate_historical_data(
        self, format_name: str, output_dir: Path, tables: Optional[list[str]] = None
    ) -> dict[str, Path]:
        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=output_dir,
            )

        all_files = self._generator.generate_all_source_data()

        result: dict[str, Path] = {}
        for file_type, file_paths in all_files.items():
            for i, file_path in enumerate(file_paths):
                key = f"{file_type}_{i}" if i > 0 else file_type
                result[key] = Path(file_path)

        return result

    def generate_incremental_data(
        self,
        format_name: str,
        output_dir: Path,
        batch_number: int,
        tables: Optional[list[str]] = None,
    ) -> dict[str, Path]:
        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=output_dir,
            )

        all_files = self._generator.generate_all_source_data()

        result: dict[str, Path] = {}
        for file_type, file_paths in all_files.items():
            for i, file_path in enumerate(file_paths):
                key = f"{file_type}_{i}_batch{batch_number}" if i > 0 else f"{file_type}_batch{batch_number}"
                result[key] = Path(file_path)

        return result

    def generate_customer_management_data(self, format_name: str, output_dir: Path, batch_number: int) -> Path:
        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=output_dir,
            )

        file_path = self._generator._generate_customer_extract()
        return Path(file_path)

    def generate_daily_market_data(self, format_name: str, output_dir: Path, batch_date: str) -> Path:
        if self._generator is None:
            self._generator = TPCDISourceDataGenerator(
                scale_factor=self.scale_factor,
                output_dir=output_dir,
                start_date=date.fromisoformat(batch_date),
                end_date=date.fromisoformat(batch_date),
            )

        file_path = self._generator._generate_market_prices()
        return Path(file_path)

    def get_data_statistics(self) -> dict[str, Any]:
        if self._generator is None:
            return {
                "scale_factor": self.scale_factor,
                "status": "not_generated",
            }

        format_info = self._generator.get_file_format_info()

        return {
            "scale_factor": self.scale_factor,
            "seed": self.seed,
            "registered_formats": list(self.formats.keys()),
            "file_formats": format_info,
            "status": "generated",
        }
