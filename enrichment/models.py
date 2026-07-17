"""Small data objects shared by the pipeline modules."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class InputRow:
    row_number: int
    input_domain: str
    domain: str


@dataclass
class Metrics:
    requests: int = 0
    request_retries: int = 0
    item_retries: int = 0


@dataclass(frozen=True)
class PipelineConfig:
    input_path: Path
    output_path: Path
    column: str
    base_url: str
    token: str
    batch_size: int
    timeout: float
    max_attempts: int
