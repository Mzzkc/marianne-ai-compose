"""Local composition corpus assembly; no model training or network operations."""

from marianne.corpus.extract import extract_in_repo
from marianne.corpus.schema import TrainingExample

__all__ = ["TrainingExample", "extract_in_repo"]
