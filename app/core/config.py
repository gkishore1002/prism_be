from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./prism.db"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    demo_password: str = "demo123"

    # Auto-bootstrap when the database has no institution (e.g. fresh prism.db + uvicorn)
    auto_bootstrap: bool = True
    bootstrap_inst_name: str = "BrightPath Academy"
    bootstrap_inst_code: str = "BRIGHTPATH"
    bootstrap_admin_name: str = "Rajesh Kumar"
    bootstrap_admin_email: str = "rajesh@brightpath.edu"
    bootstrap_tutor_name: str = "Priya Sharma"
    bootstrap_tutor_email: str = "priya@brightpath.edu"
    bootstrap_student_name: str = "Arjun Mehta"
    bootstrap_student_email: str = "arjun@brightpath.edu"

    # Vertex AI (Gemini summaries for student reports)
    vertex_enabled: bool = True
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    vertex_model: str = "gemini-2.5-flash"
    vertex_summary_cache_ttl: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
