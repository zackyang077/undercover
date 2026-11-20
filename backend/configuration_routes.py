"""Serve run configuration, version history and handler-owned settings."""

from fastapi import APIRouter

from backend import configuration as config
from backend.auth import CurrentHandler, SeniorHandler
from backend.configuration import BusinessSettings
from backend.storage import Store


def routes(store: Store):
    router = APIRouter(prefix="/api")

    @router.get(
        "/agent/configuration/current", operation_id="current_agent_configuration"
    )
    def current_agent_configuration():
        """Read the current execution configuration without scores."""
        with store.connection():
            return config.effective_config(store, config.current(store))

    @router.get(
        "/agent/configuration/{version}", operation_id="get_agent_configuration"
    )
    def get_agent_configuration(version: int):
        """Read a single version for the claim worker."""
        return config.effective_config(store, version)

    @router.get("/configuration", operation_id="configuration_history")
    def configuration_history(handler: CurrentHandler):
        """List the current version and all configuration changes."""
        with store.connection():
            return {
                "current": config.current(store),
                "versions": config.history(store, "configuration"),
            }

    @router.get("/settings", operation_id="get_platform_settings")
    def get_platform_settings(handler: CurrentHandler):
        """Read platform settings and the model catalog."""
        with store.connection():
            return config.settings_view(store)

    @router.put("/settings", operation_id="change_platform_settings")
    def change_platform_settings(body: BusinessSettings, handler: SeniorHandler):
        """Change business settings as a senior handler."""
        return config.update_settings(store, body, handler.handler_id)

    return router
