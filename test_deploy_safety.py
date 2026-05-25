from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent


def test_dockerfile_rejects_shadowing_data_package_at_build_time():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "test ! -e /app/data" in dockerfile
    assert "from core.engine import Engine" in dockerfile


def test_compose_can_build_the_current_checkout():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["multi-strategy"]

    assert service["build"]["context"] == "."


def test_deploy_workflow_rebuilds_from_current_checkout():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "docker compose up -d --build --remove-orphans" in workflow
