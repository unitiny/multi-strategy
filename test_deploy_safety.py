from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent


def test_dockerfile_rejects_shadowing_data_package_at_build_time():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "test ! -e /app/data" in dockerfile
    assert "from core.engine import Engine" in dockerfile


def test_compose_uses_prebuilt_image_by_default():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["multi-strategy"]

    assert "build" not in service
    assert service["image"] == "ghcr.io/unitiny/multi-strategy:${IMAGE_TAG:-latest}"


def test_deploy_workflow_pulls_prebuilt_sha_image():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )

    assert "IMAGE_TAG=${{ github.sha }} docker compose pull" in workflow
    assert "IMAGE_TAG=${{ github.sha }} docker compose up -d --remove-orphans" in workflow
    assert "--build" not in workflow
