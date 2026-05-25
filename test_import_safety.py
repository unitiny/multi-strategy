import importlib.util
from pathlib import Path


def test_project_does_not_shadow_third_party_data_package():
    spec = importlib.util.find_spec("data")

    if spec and spec.origin:
        project_root = Path(__file__).resolve().parent
        data_origin = Path(spec.origin).resolve()

        assert not data_origin.is_relative_to(project_root), (
            "project must not expose a top-level 'data' package"
        )
