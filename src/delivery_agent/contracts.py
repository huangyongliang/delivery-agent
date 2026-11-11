"""Parameter names shared by the agent and the Jenkinsfile template.

The Jenkinsfile cannot import this module. tests/test_contracts.py checks that
the template still declares every name.
"""

from __future__ import annotations

PARAM_PRODUCT_NAME = "PRODUCT_NAME"
PARAM_GIT_URL = "GIT_URL"
PARAM_GIT_REF = "GIT_REF"
PARAM_GIT_SHA = "GIT_SHA"
PARAM_IMAGE_REPO = "IMAGE_REPO"
PARAM_IMAGE_TAG = "IMAGE_TAG"
PARAM_TARGET_ENV = "TARGET_ENV"
PARAM_DELIVERY_ID = "DELIVERY_ID"
PARAM_AGENT_CALLBACK_URL = "AGENT_CALLBACK_URL"

JENKINS_PARAMETERS: tuple[str, ...] = (
    PARAM_PRODUCT_NAME,
    PARAM_GIT_URL,
    PARAM_GIT_REF,
    PARAM_GIT_SHA,
    PARAM_IMAGE_REPO,
    PARAM_IMAGE_TAG,
    PARAM_TARGET_ENV,
    PARAM_DELIVERY_ID,
    PARAM_AGENT_CALLBACK_URL,
)

# Fixed execution stages. A model may describe them, but cannot add new ones.
STAGE_KEYS: tuple[str, ...] = (
    "resolve_source",
    "plan",
    "gitlab_pipeline",
    "jenkins_build",
    "docker_publish",
    "report",
)

STAGE_TITLES: dict[str, str] = {
    "resolve_source": "解析代码仓库",
    "plan": "生成交付计划",
    "gitlab_pipeline": "触发 GitLab 流水线",
    "jenkins_build": "执行 Jenkins 构建",
    "docker_publish": "确认镜像发布",
    "report": "汇总交付结果",
}
