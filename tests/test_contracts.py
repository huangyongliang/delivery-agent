"""Keep the Jenkins template aligned with the parameters the agent sends."""

from __future__ import annotations

from pathlib import Path

from delivery_agent.contracts import JENKINS_PARAMETERS

ROOT = Path(__file__).resolve().parents[1]


def test_jenkins_templates_match_the_agent_contract() -> None:
    sample = (ROOT / "examples" / "sample-product" / "Jenkinsfile").read_text(encoding="utf-8")
    template_path = ROOT / "examples" / "pipelines" / "docker-delivery.Jenkinsfile"
    template = template_path.read_text(encoding="utf-8")
    assert sample == template
    for name in JENKINS_PARAMETERS:
        assert f"name: '{name}'" in sample
    assert "pushed image" in sample
    assert "JENKINS_WEBHOOK_TOKEN" in sample
    assert "X-Jenkins-Token" in sample
    gitlab_ci_path = ROOT / "examples" / "sample-product" / ".gitlab-ci.yml"
    gitlab_ci = gitlab_ci_path.read_text(encoding="utf-8")
    assert "PRODUCT_NAME" in gitlab_ci
    dockerfile = (ROOT / "examples" / "sample-product" / "Dockerfile").read_text(encoding="utf-8")
    assert "CMD" in dockerfile
