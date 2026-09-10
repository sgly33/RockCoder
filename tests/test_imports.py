import importlib
from pathlib import Path


def test_rockcoder_package_imports() -> None:
    module = importlib.import_module("rockcoder")
    assert module is not None


def test_pyproject_exposes_rockcoder_cli() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    pyproject = pyproject_path.read_text(encoding="utf-8")

    assert 'rockcoder = "rockcoder.__main__:main"' in pyproject
