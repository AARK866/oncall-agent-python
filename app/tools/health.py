from app.config import settings
from app.schemas import OpsToolHealthResponse, ToolBackendStatus
from app.tools.factory import create_ops_tool_registry


def get_ops_tool_health(mode: str | None = None) -> OpsToolHealthResponse:
    registry = create_ops_tool_registry(mode)
    backends = _backends_for_mode(registry.mode)
    ready = all(backend.configured for backend in backends)

    return OpsToolHealthResponse(
        mode=registry.mode,
        connector_name=registry.connector_name,
        ready=ready,
        tools=registry.list_tools(),
        tool_schemas=registry.tool_schemas(),
        backends=backends,
        message=_message(registry.mode, ready),
    )


def _backends_for_mode(mode: str) -> list[ToolBackendStatus]:
    if mode == "mock":
        return [
            ToolBackendStatus(
                name="mock_data",
                configured=True,
                notes="Mock tools read local JSON fixtures from app/data.",
            )
        ]

    if mode == "real":
        return [
            _backend(
                name="prometheus",
                required_settings=["PROMETHEUS_BASE_URL"],
                values={"PROMETHEUS_BASE_URL": settings.prometheus_base_url},
            ),
            _backend(
                name="loki",
                required_settings=["LOKI_BASE_URL"],
                values={"LOKI_BASE_URL": settings.loki_base_url},
            ),
            _backend(
                name="gitlab",
                required_settings=["GITLAB_BASE_URL", "GITLAB_PROJECT_ID"],
                optional_settings=["GITLAB_TOKEN"],
                values={
                    "GITLAB_BASE_URL": settings.gitlab_base_url,
                    "GITLAB_PROJECT_ID": settings.gitlab_project_id,
                    "GITLAB_TOKEN": settings.gitlab_token,
                },
            ),
            _backend(
                name="github",
                required_settings=["GITHUB_REPO"],
                optional_settings=["GITHUB_TOKEN", "GITHUB_BRANCH", "GITHUB_BASE_URL"],
                values={
                    "GITHUB_REPO": settings.github_repo,
                    "GITHUB_TOKEN": settings.github_token,
                    "GITHUB_BRANCH": settings.github_branch,
                    "GITHUB_BASE_URL": settings.github_base_url,
                },
            ),
            ToolBackendStatus(
                name="topology",
                configured=True,
                notes="Topology currently uses a placeholder until CMDB/Kubernetes/service graph is connected.",
            ),
        ]

    return [
        ToolBackendStatus(
            name="unknown",
            configured=False,
            notes=f"Unsupported tool mode: {mode}",
        )
    ]


def _backend(
    name: str,
    required_settings: list[str],
    values: dict[str, str | None],
    optional_settings: list[str] | None = None,
) -> ToolBackendStatus:
    missing = [
        setting_name
        for setting_name in required_settings
        if not values.get(setting_name)
    ]
    return ToolBackendStatus(
        name=name,
        configured=not missing,
        required_settings=required_settings,
        optional_settings=optional_settings or [],
        missing_settings=missing,
    )


def _message(mode: str, ready: bool) -> str:
    if ready:
        return f"Ops tool connector '{mode}' is ready."
    return f"Ops tool connector '{mode}' is registered, but some backend settings are missing."
