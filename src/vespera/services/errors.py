"""Errors the services raise instead of printing a message and exiting.

Each message is written for the person running Vespera and has no "[FAIL]" prefix: the command
line adds the prefix, the web app shows the message as it is.
"""


class ServiceError(Exception):
    """A problem the user can fix. `tip` is an optional second line with advice."""

    def __init__(self, message: str, tip: str | None = None) -> None:
        super().__init__(message)
        self.tip = tip


class ProjectFileError(ServiceError):
    """vespera.yaml cannot be used."""


class TargetError(ServiceError):
    """The target (the bot or model under test) cannot be built from the settings given."""


class MissingApiKey(ServiceError):
    """The target needs an API key, and the environment variable that should hold it is empty."""

    def __init__(self, env_name: str) -> None:
        super().__init__(f"set the environment variable {env_name} with your API key")
        self.env_name = env_name


class ResultError(ServiceError):
    """A report, a gate decision or an export cannot be built for this run."""


class InputFileError(ServiceError):
    """The scenario file or the system prompt file cannot be read or is not valid."""


class RunNotFound(ServiceError):
    """The run asked for is not in the database."""


class ResumeMismatch(ServiceError):
    """A run cannot be continued because something that matters has changed."""


class RunStopped(ServiceError):
    """The run stopped before the end, for example after several errors in a row."""


class CheckError(ServiceError):
    """The checks cannot judge this run, for example because the system prompt differs."""
