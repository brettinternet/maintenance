"""Strict YAML configuration loading for the maintenance orchestrator."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when the maintenance configuration is invalid."""


class _UniqueSafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise ConfigError("YAML mapping keys must be scalar values") from error
            if duplicate:
                raise ConfigError(f"duplicate YAML key: {key!r}")
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


@dataclass(frozen=True)
class RepositoryConfig:
    """One fully resolved repository entry."""

    owner: str
    name: str
    enabled: bool
    model: str
    commit_author_name: str
    commit_author_email: str
    base_branch: str | None
    checks: tuple[tuple[str, ...], ...]
    draft_pr: bool
    runs_on: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.name}"

    def matrix_entry(self, dry_run: bool) -> dict[str, Any]:
        """Return the JSON-safe object consumed by a GitHub Actions matrix."""

        return {
            "repository": self.repository,
            "owner": self.owner,
            "name": self.name,
            "enabled": self.enabled,
            "model": self.model,
            "commit_author_name": self.commit_author_name,
            "commit_author_email": self.commit_author_email,
            "base_branch": self.base_branch,
            "checks": [list(command) for command in self.checks],
            "draft_pr": self.draft_pr,
            "runs_on": self.runs_on,
            "dry_run": dry_run,
        }


@dataclass(frozen=True)
class MaintenanceConfig:
    """Validated configuration and resolved repository overrides."""

    version: int
    defaults: RepositoryConfig
    repositories: tuple[RepositoryConfig, ...]

    def matrix(self, repository_filter: str | None = None, dry_run: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Build the Actions ``include`` matrix after applying filtering."""

        normalized_filter = parse_repository_filter(repository_filter)
        entries = [
            repository.matrix_entry(dry_run)
            for repository in self.repositories
            if repository.enabled
            and (normalized_filter is None or repository.repository.casefold() == normalized_filter.casefold())
        ]
        return {"include": entries}


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{location} must be a mapping")
    return value


def _check_keys(value: Mapping[str, Any], allowed: set[str], location: str, required: set[str] | None = None) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        joined = ", ".join(unknown)
        raise ConfigError(f"{location} has unknown key(s): {joined}")
    missing = sorted(key for key in (required or set()) if key not in value)
    if missing:
        joined = ", ".join(missing)
        raise ConfigError(f"{location} is missing required key(s): {joined}")


def _string(value: Any, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{location} must be a string")
    if not allow_empty and not value:
        raise ConfigError(f"{location} must not be empty")
    if "\x00" in value or any(ord(character) < 32 for character in value):
        raise ConfigError(f"{location} must not contain control characters")
    return value


_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$")


def _owner(value: Any, location: str) -> str:
    candidate = _string(value, location)
    if len(candidate) > 39 or not _OWNER_RE.fullmatch(candidate):
        raise ConfigError(f"{location} is not a valid GitHub owner name")
    return candidate


def _repository_name(value: Any, location: str) -> str:
    candidate = _string(value, location)
    if len(candidate) > 100 or not _REPOSITORY_RE.fullmatch(candidate):
        raise ConfigError(f"{location} is not a valid GitHub repository name")
    return candidate


def parse_repository_identifier(value: Any, location: str = "repository") -> str:
    """Validate and normalize an ``owner/name`` GitHub repository identifier."""

    if not isinstance(value, str) or value.count("/") != 1:
        raise ConfigError(f"{location} must be owner/name")
    owner_value, name_value = value.split("/", 1)
    owner = _owner(owner_value, f"{location} owner")
    name = _repository_name(name_value, f"{location} name")
    return f"{owner}/{name}"


def _branch(value: Any, location: str) -> str | None:
    if value is None:
        return None
    candidate = _string(value, location)
    if len(candidate) > 255:
        raise ConfigError(f"{location} is too long")
    if (
        candidate.startswith("/")
        or candidate.endswith("/")
        or candidate.startswith(".")
        or candidate.endswith(".")
        or candidate.endswith(".lock")
        or "//" in candidate
        or ".." in candidate
        or "@{" in candidate
        or any(character in " ~^:?*[\\" for character in candidate)
        or any(segment in {".", ".."} for segment in candidate.split("/"))
        or any(segment.startswith("-") for segment in candidate.split("/"))
    ):
        raise ConfigError(f"{location} is not a valid Git branch name")
    return candidate


def _email(value: Any, location: str) -> str:
    candidate = _string(value, location)
    if any(character.isspace() for character in candidate) or candidate.count("@") != 1:
        raise ConfigError(f"{location} must be an email address")
    local, domain = candidate.split("@")
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        raise ConfigError(f"{location} must be an email address")
    return candidate


def _author(value: Any, location: str, inherited: Mapping[str, str]) -> dict[str, str]:
    author = _mapping(value, location)
    _check_keys(author, {"name", "email"}, location)
    resolved = {
        "name": inherited["name"],
        "email": inherited["email"],
    }
    if "name" in author:
        resolved["name"] = _string(author["name"], f"{location}.name")
    if "email" in author:
        resolved["email"] = _email(author["email"], f"{location}.email")
    return resolved


def _checks(value: Any, location: str) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        raise ConfigError(f"{location} must be a list of argv arrays")
    resolved: list[tuple[str, ...]] = []
    for command_index, command in enumerate(value):
        command_location = f"{location}[{command_index}]"
        if not isinstance(command, list) or not command:
            raise ConfigError(f"{command_location} must be a non-empty argv array")
        argv = tuple(
            _string(argument, f"{command_location}[{argument_index}]", allow_empty=True)
            for argument_index, argument in enumerate(command)
        )
        if not argv[0]:
            raise ConfigError(f"{command_location}[0] must not be empty")
        resolved.append(argv)
    return tuple(resolved)


def _options(value: Any, location: str, inherited: Mapping[str, Any]) -> dict[str, Any]:
    options = _mapping(value, location)
    allowed = {"enabled", "model", "commit_author", "base_branch", "checks", "draft_pr", "runs_on"}
    _check_keys(options, allowed, location)
    resolved = {
        "enabled": inherited["enabled"],
        "model": inherited["model"],
        "commit_author": dict(inherited["commit_author"]),
        "base_branch": inherited["base_branch"],
        "checks": inherited["checks"],
        "draft_pr": inherited["draft_pr"],
        "runs_on": inherited["runs_on"],
    }
    if "enabled" in options:
        if not isinstance(options["enabled"], bool):
            raise ConfigError(f"{location}.enabled must be a boolean")
        resolved["enabled"] = options["enabled"]
    if "model" in options:
        resolved["model"] = _string(options["model"], f"{location}.model")
    if "commit_author" in options:
        resolved["commit_author"] = _author(
            options["commit_author"],
            f"{location}.commit_author",
            resolved["commit_author"],
        )
    if "base_branch" in options:
        resolved["base_branch"] = _branch(options["base_branch"], f"{location}.base_branch")
    if "checks" in options:
        resolved["checks"] = _checks(options["checks"], f"{location}.checks")
    if "draft_pr" in options:
        if not isinstance(options["draft_pr"], bool):
            raise ConfigError(f"{location}.draft_pr must be a boolean")
        resolved["draft_pr"] = options["draft_pr"]
    if "runs_on" in options:
        resolved["runs_on"] = _string(options["runs_on"], f"{location}.runs_on")
    return resolved


def _repository_config(owner: str, name: str, options: Mapping[str, Any]) -> RepositoryConfig:
    return RepositoryConfig(
        owner=owner,
        name=name,
        enabled=options["enabled"],
        model=options["model"],
        commit_author_name=options["commit_author"]["name"],
        commit_author_email=options["commit_author"]["email"],
        base_branch=options["base_branch"],
        checks=options["checks"],
        draft_pr=options["draft_pr"],
        runs_on=options["runs_on"],
    )


_BUILTIN_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "model": "auto",
    "commit_author": {
        "name": "Maintenance Bot",
        "email": "maintenance-bot@users.noreply.github.com",
    },
    "base_branch": None,
    "checks": tuple(),
    "draft_pr": True,
    "runs_on": "ubuntu-latest",
}


def _parse_document(document: Any, source: str) -> MaintenanceConfig:
    root = _mapping(document, source)
    _check_keys(root, {"version", "defaults", "repositories"}, source, {"version", "defaults", "repositories"})
    version = root["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError(f"{source}.version must be the integer 1")

    defaults_raw = _mapping(root["defaults"], f"{source}.defaults")
    defaults = _options(defaults_raw, f"{source}.defaults", _BUILTIN_DEFAULTS)
    defaults_config = _repository_config("defaults", "defaults", defaults)

    repositories_raw = root["repositories"]
    if not isinstance(repositories_raw, list):
        raise ConfigError(f"{source}.repositories must be a list")
    repositories: list[RepositoryConfig] = []
    seen: set[str] = set()
    for index, raw_repository in enumerate(repositories_raw):
        location = f"{source}.repositories[{index}]"
        repository = _mapping(raw_repository, location)
        _check_keys(
            repository,
            {"owner", "name", "enabled", "model", "commit_author", "base_branch", "checks", "draft_pr", "runs_on"},
            location,
            {"owner", "name"},
        )
        owner = _owner(repository["owner"], f"{location}.owner")
        name = _repository_name(repository["name"], f"{location}.name")
        identity = f"{owner}/{name}".casefold()
        if identity in seen:
            raise ConfigError(f"{location} duplicates repository {owner}/{name}")
        seen.add(identity)
        overrides = {key: value for key, value in repository.items() if key not in {"owner", "name"}}
        options = _options(overrides, location, defaults)
        if options["enabled"] and not options["checks"]:
            raise ConfigError(f"{location}.checks must contain at least one command when enabled")
        repositories.append(_repository_config(owner, name, options))

    return MaintenanceConfig(version=version, defaults=defaults_config, repositories=tuple(repositories))


def load_config(path: str | Path = "config.yaml") -> MaintenanceConfig:
    """Load and strictly validate a YAML configuration file."""

    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=_UniqueSafeLoader)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {config_path}") from error
    except OSError as error:
        raise ConfigError(f"could not read configuration file {config_path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {config_path}: {error}") from error
    return _parse_document(document, str(config_path))


def parse_repository_filter(value: str | None) -> str | None:
    """Validate an optional ``owner/name`` filter and return its normalized form."""

    if value is None or value == "":
        return None
    return parse_repository_identifier(value, "repository filter")


def matrix_json(config: MaintenanceConfig, repository_filter: str | None = None, dry_run: bool = False) -> str:
    """Serialize an Actions matrix as compact JSON."""

    return json.dumps(config.matrix(repository_filter, dry_run), separators=(",", ":"), sort_keys=True)
