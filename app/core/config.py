from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./prism.db"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174"
    demo_password: str = "demo123"

    # Swotify-style idempotent demo seed — on by default (set SEED_DEMO=false in production).
    seed_demo: bool = True
    seed_super_admin_email: str = "superuser@prism.io"
    seed_super_admin_password: str = "superuser123"
    seed_super_admin_name: str = "Super Admin"
    seed_demo_org_code: str = "DEMO001"
    seed_demo_org_name: str = "Demo Education Group"
    seed_demo_admin_name: str = "Demo Admin"
    seed_demo_admin_email: str = "admin@demo.com"
    seed_demo_tutor_name: str = "Demo Tutor"
    seed_demo_tutor_email: str = "tutor@demo.com"
    seed_demo_student_name: str = "Demo Student"
    seed_demo_student_email: str = "student@demo.com"
    seed_demo_password: str = "demo1234"

    # Default org code suggested during first-run /setup wizard (production custom orgs).
    default_organization_code: str = "CSC"
    auto_bootstrap: bool = False
    csc_scheduler_enabled: bool = True
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
    vertex_request_timeout_seconds: int = 12
    vertex_book_timeout_seconds: int = 180
    vertex_topic_map_timeout_seconds: int = 90
    google_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
