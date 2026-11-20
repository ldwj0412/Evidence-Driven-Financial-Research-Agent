import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator

from .domain import Record


class Credential(Record):
    api_key: SecretStr = SecretStr("")

    @field_validator("api_key", mode="before")
    @classmethod
    def strip_key(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if value and (len(value) < 8 or not value.isascii() or any(char.isspace() for char in value)):
            raise ValueError("Use the full provider API key without spaces")
        return value


class RouterSettings(Credential):
    base_url: str = "https://openrouter.ai/api/v1"
    model: str = Field(default="openai/gpt-5-mini", pattern=r"^[\w.-]+/[\w.:-]+$", max_length=100)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value):
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.query or parts.fragment:
            raise ValueError("Use an HTTPS base URL without credentials, query, or fragment")
        return value.rstrip("/")


class SecSettings(Record):
    user_agent: str = Field(default="", max_length=200)

    @field_validator("user_agent")
    @classmethod
    def valid_identification(cls, value):
        value = value.strip()
        if value and ("@" not in value or not value.isascii() or not value.isprintable()):
            raise ValueError("Use an organization name and contact email")
        return value


@dataclass(frozen=True)
class Settings:
    openrouter: RouterSettings
    fred: Credential
    tavily: Credential
    sec: SecSettings
    messages: tuple[str, ...]

    def redact(self, value):
        if isinstance(value, str):
            for item in (self.openrouter.api_key, self.fred.api_key, self.tavily.api_key):
                secret = item.get_secret_value()
                if secret:
                    for variant in {secret, quote(secret, safe=""), quote_plus(secret)}:
                        value = value.replace(variant, "[redacted]")
            return value
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {self.redact(key): self.redact(item) for key, item in value.items()}
        return value


def load_config(path: Path) -> Settings:
    messages = []
    try:
        with path.open("rb") as stream:
            content = tomllib.load(stream)
    except FileNotFoundError:
        content = {}
    except (OSError, ValueError):
        content = {}
        messages.append("Read config.local.toml and fix its TOML syntax or file permissions.")
    specs = (
        ("openrouter", "OpenRouter", RouterSettings, "api_key", "OPENROUTER_API_KEY"),
        ("fred", "FRED", Credential, "api_key", "FRED_API_KEY"),
        ("tavily", "Tavily", Credential, "api_key", "TAVILY_API_KEY"),
        ("sec", "SEC EDGAR", SecSettings, "user_agent", "SEC_USER_AGENT"),
    )
    if set(content) - {item[0] for item in specs}:
        messages.append("Use the provider sections listed in config.example.toml.")
    providers = {}
    for key, label, schema, credential, variable in specs:
        section = content.get(key, {})
        if isinstance(section, dict):
            value = section.get(credential, "")
            if isinstance(value, str):
                reference = re.fullmatch(r"\$\{([a-zA-Z_][a-zA-Z_0-9]*)\}", value.strip())
                if reference:
                    variable = reference[1]
                if reference or not value.strip():
                    section = {**section, credential: os.environ.get(variable, "")}
        try:
            provider = schema.model_validate(section)
        except (ValidationError, ValueError):
            messages.append(
                f"{label} configuration is invalid. Check [{key}] in config.local.toml and {variable}."
            )
            provider = schema()
        else:
            value = getattr(provider, credential)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value:
                messages.append(
                    f"{label} is unavailable. Set {variable} or update {key}.{credential} in config.local.toml."
                )
        providers[key] = provider
    return Settings(**providers, messages=tuple(messages))
