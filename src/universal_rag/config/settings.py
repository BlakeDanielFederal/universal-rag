"""Process-wide settings sourced from the environment / `.env`.

Secrets and infra endpoints live here. Project & source *structure* lives in
`config/config.yaml` (see `schema.py`), which references these by env-var name.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Postgres ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "universal_rag"
    postgres_user: str = "urag"
    postgres_password: str = "change-me"
    database_url: str | None = None  # explicit override wins

    # --- Ollama (embeddings + optional retrieval reranker) ---
    ollama_host: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    # Optional retrieval reranker: an Ollama chat model used as a relevance judge.
    # Blank -> no reranking (RRF order is used as-is).
    rerank_model: str = ""
    # nomic-embed-text quality depends on task prefixes; set empty for models
    # (e.g. mxbai-embed-large) that don't use them.
    embed_doc_prefix: str = "search_document: "
    embed_query_prefix: str = "search_query: "

    # --- Evaluation harness (offline; not on the retrieval core path) ---
    # Ollama chat model used to synthesize the golden set (`urag eval gen`).
    eval_gen_model: str = ""
    # Optional judge model for context-support metric; blank -> retrieval metrics only.
    # Local-first default; a hosted judge can be plugged via the Judge protocol.
    eval_judge_model: str = ""

    # --- Connector creds (optional; only the ones in use need to be set) ---
    # Confluence Data Center: Personal Access Token via `Authorization: Bearer`.
    confluence_base_url: str = ""  # e.g. https://confluence.your-org.com
    confluence_pat: str = ""
    confluence_verify_ssl: bool = True  # set false only for internal/self-signed CAs
    # Jira Data Center: Personal Access Token via `Authorization: Bearer`.
    jira_base_url: str = ""  # e.g. https://jira.your-org.com
    jira_pat: str = ""
    jira_verify_ssl: bool = True
    # GitHub: PAT (classic or fine-grained) via `Authorization: Bearer`.
    # api_url is https://api.github.com for github.com, or https://<host>/api/v3 for GHE.
    github_token: str = ""
    github_api_url: str = "https://api.github.com"
    # Microsoft Graph (SharePoint/OneDrive): app-only client-credentials flow.
    # Needs an Azure AD app with admin-consented Sites.Read.All + Files.Read.All.
    msgraph_tenant_id: str = ""
    msgraph_client_id: str = ""
    msgraph_client_secret: str = ""
    msgraph_authority: str = "https://login.microsoftonline.com"
    msgraph_base_url: str = "https://graph.microsoft.com/v1.0"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # path to the YAML project config; overridable via URAG_CONFIG_PATH
    config_path: str = Field(default="config/config.yaml", alias="URAG_CONFIG_PATH")


@lru_cache
def get_settings() -> Settings:
    return Settings()
