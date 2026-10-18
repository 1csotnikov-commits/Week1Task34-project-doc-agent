"""Конфигурация проекта (`docagent.yaml`) и провайдеров."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from project_doc_agent.errors import ConfigError
from project_doc_agent.models import Profile
from project_doc_agent.resources import providers_path
from project_doc_agent.utils import is_slug


def _relative_posix(value: str, field_name: str) -> str:
    raw = value.replace("\\", "/").strip()
    if not raw:
        raise ValueError(f"{field_name} не может быть пустым")
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"{field_name} должен быть относительным путём внутри workspace: {value!r}")
    return pure.as_posix()


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client: str
    client_name: str = Field(min_length=1)
    project: str
    project_name: str = Field(min_length=1)
    bases: list[str] = Field(min_length=1)
    profile: Profile = Profile.integration
    provider: str = "deepseek"
    source_dir: str = Field(min_length=1)
    output_dir: str = "cards"
    reports_dir: str = "reports"
    obsidian_folder: str = ""
    language: str = "ru"
    max_questions: int = Field(default=20, ge=0, le=200)

    # Операционные параметры MVP (можно переопределить в docagent.yaml).
    max_file_size_mb: int = Field(default=50, ge=1, le=2048)
    max_chunk_chars: int = Field(default=6000, ge=1000, le=100_000)
    max_steps: int = Field(default=20, ge=1, le=100)
    max_tokens_total: int = Field(default=300_000, ge=1000)
    txt_detect_headings: bool = False
    save_llm_raw: bool = False

    @field_validator("client", "project")
    @classmethod
    def _slug_required(cls, value: str) -> str:
        if not is_slug(value):
            raise ValueError(f"Ожидается slug (латиница, цифры, дефисы): {value!r}")
        return value

    @field_validator("provider")
    @classmethod
    def _provider_slug(cls, value: str) -> str:
        if not is_slug(value):
            raise ValueError(f"Некорректное имя провайдера: {value!r}")
        return value

    @field_validator("bases")
    @classmethod
    def _bases_valid(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("bases должен содержать хотя бы одну базу")
        result: list[str] = []
        for item in value:
            cleaned = item.strip()
            if not is_slug(cleaned):
                raise ValueError(f"Некорректный slug базы: {item!r}")
            if cleaned not in result:
                result.append(cleaned)
        return result

    @field_validator("output_dir", "reports_dir")
    @classmethod
    def _output_relative(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "output_dir")
        return _relative_posix(value, field_name)

    @field_validator("obsidian_folder", mode="before")
    @classmethod
    def _obsidian_relative(cls, value: str | None) -> str:
        if value is None or value == "":
            return ""
        return _relative_posix(str(value), "obsidian_folder")

    @model_validator(mode="after")
    def _dirs_not_equal(self) -> ProjectConfig:
        if self.output_dir == self.reports_dir:
            raise ValueError("output_dir и reports_dir должны различаться")
        if not self.obsidian_folder:
            self.obsidian_folder = f"{self.client}/{self.project}/{self.output_dir}"
        return self

    @property
    def project_id(self) -> str:
        return f"{self.client}-{self.project}"

    def yaml_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    def source_path(self, workspace_root: Path) -> Path:
        raw = Path(self.source_dir).expanduser()
        if not raw.is_absolute():
            raw = workspace_root / raw
        return raw.resolve()


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    json_mode: bool = True
    tools: bool = False


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    base_url: str
    api_key_env: str
    model: str
    max_tokens: int = Field(default=8192, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    input_cost_per_1m: float = Field(default=0.0, ge=0.0)
    output_cost_per_1m: float = Field(default=0.0, ge=0.0)
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)

    @field_validator("name")
    @classmethod
    def _name_slug(cls, value: str) -> str:
        if not is_slug(value):
            raise ValueError(f"Некорректное имя провайдера: {value!r}")
        return value


class ProvidersFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    providers: list[ProviderConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> ProvidersFile:
        names = [provider.name for provider in self.providers]
        if len(names) != len(set(names)):
            raise ValueError("Имена провайдеров должны быть уникальными")
        return self

    def by_name(self, name: str) -> ProviderConfig:
        for provider in self.providers:
            if provider.name == name:
                return provider
        available = ", ".join(provider.name for provider in self.providers)
        raise ConfigError(f"Провайдер {name!r} не найден в providers.yaml. Доступны: {available}")


def load_providers(path: Path | None = None) -> ProvidersFile:
    config_path = path or providers_path()
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Некорректный YAML в {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Ожидается объект providers в {config_path}")
    try:
        return ProvidersFile.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Некорректная конфигурация провайдеров {config_path}: {exc}") from exc


def load_project_config(workspace_root: Path, check_source: bool = True) -> ProjectConfig:
    config_path = workspace_root / "docagent.yaml"
    if not config_path.is_file():
        raise ConfigError(
            f"Не найден {config_path}. Выполните `docagent init --workspace {workspace_root}`."
        )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Не удалось прочитать {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Некорректный YAML в {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Ожидается YAML-объект в {config_path}")
    try:
        config = ProjectConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"Некорректная конфигурация {config_path}:\n{exc}") from exc

    providers = load_providers()
    providers.by_name(config.provider)
    if check_source:
        source = config.source_path(workspace_root)
        if not source.exists():
            raise ConfigError(f"source_dir не существует: {source}")
        if not source.is_dir():
            raise ConfigError(f"source_dir не является папкой: {source}")
    return config


def save_project_config(workspace_root: Path, config: ProjectConfig) -> Path:
    config_path = workspace_root / "docagent.yaml"
    content = yaml.safe_dump(
        config.yaml_dict(),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path.with_name(config_path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(config_path)
    return config_path


def effective_source_dir(config: ProjectConfig, workspace_root: Path, override: Path | None = None) -> Path:
    source = override.resolve() if override is not None else config.source_path(workspace_root)
    if not source.exists():
        raise ConfigError(f"source_dir не существует: {source}")
    if not source.is_dir():
        raise ConfigError(f"source_dir не является папкой: {source}")
    return source


def make_project_config(
    *,
    client: str,
    client_name: str,
    project: str,
    project_name: str,
    bases: list[str],
    profile: str | Profile = Profile.integration,
    provider: str = "deepseek",
    source_dir: str,
    obsidian_folder: str | None = None,
) -> ProjectConfig:
    return ProjectConfig(
        client=client.strip(),
        client_name=client_name.strip(),
        project=project.strip(),
        project_name=project_name.strip(),
        bases=[item.strip() for item in bases if item.strip()],
        profile=Profile(profile),
        provider=provider.strip(),
        source_dir=source_dir.replace("\\", "/"),
        obsidian_folder=obsidian_folder or "",
    )
