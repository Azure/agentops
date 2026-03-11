"""AgentOps CLI package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agentops-toolkit")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
