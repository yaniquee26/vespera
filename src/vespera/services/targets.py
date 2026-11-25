"""API keys for targets. A key is only ever read from an environment variable, never stored."""

import os

from vespera.services.errors import MissingApiKey


def api_key_from_env(name: str | None) -> str | None:
    """The key in the variable `name`. No name means the target needs no key."""
    if name is None:
        return None
    key = os.environ.get(name)
    if not key:
        raise MissingApiKey(name)
    return key


def api_key_for(address: str, api_key_env: str) -> str | None:
    """The key for an API address. A server on this computer needs none, any other server does."""
    key = os.environ.get(api_key_env)
    if not key and "localhost" not in address and "127.0.0.1" not in address:
        raise MissingApiKey(api_key_env)
    return key


def env_var_is_set(name: str) -> bool:
    """Whether a variable has a value. Callers show only yes or no, never the value itself."""
    return bool(os.environ.get(name))
