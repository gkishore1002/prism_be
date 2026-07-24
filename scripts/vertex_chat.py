"""Terminal chat with Gemini 2.5 Flash on Vertex AI (Google Cloud).

Prerequisites:
  pip install google-genai
  gcloud auth login
  gcloud auth application-default login   (optional if gcloud login is active)
  gcloud config set project YOUR_PROJECT_ID

Optional env vars:
  GOOGLE_CLOUD_PROJECT   GCP project (falls back to gcloud config)
  GOOGLE_CLOUD_LOCATION  Region, default: us-central1
  VERTEX_MODEL           Model id, default: gemini-2.5-flash
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")


def resolve_project() -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if project:
        return project
    gcloud = "gcloud.cmd" if os.name == "nt" else "gcloud"
    try:
        result = subprocess.run(
            [gcloud, "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=False,
            shell=os.name == "nt",
        )
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def resolve_credentials():
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
    from google.oauth2.credentials import Credentials

    try:
        credentials, _ = google.auth.default()
        return credentials
    except DefaultCredentialsError:
        pass

    gcloud = "gcloud.cmd" if os.name == "nt" else "gcloud"
    try:
        result = subprocess.run(
            [gcloud, "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=False,
            shell=os.name == "nt",
        )
    except FileNotFoundError:
        return None

    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        return None
    return Credentials(token=token)


def create_client():
    from google import genai

    project = resolve_project()
    if not project:
        print(
            "Missing GCP project. Set GOOGLE_CLOUD_PROJECT or run:\n"
            "  gcloud config set project YOUR_PROJECT_ID",
            file=sys.stderr,
        )
        sys.exit(1)

    credentials = resolve_credentials()
    if credentials is None:
        print(
            "No Google credentials found. Run:\n"
            "  gcloud auth login\n"
            "  gcloud auth application-default login",
            file=sys.stderr,
        )
        sys.exit(1)

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", LOCATION)

    return genai.Client(
        vertexai=True,
        project=project,
        location=LOCATION,
        credentials=credentials,
    )


def print_banner(project: str) -> None:
    print("=" * 60)
    print("Vertex AI chat  (terminal only)")
    print(f"  project : {project}")
    print(f"  location: {LOCATION}")
    print(f"  model   : {MODEL}")
    print("  commands: exit | quit | clear")
    print("=" * 60)


def run_chat() -> None:
    try:
        from google.genai import types
    except ImportError:
        print("Install the SDK first:\n  pip install google-genai", file=sys.stderr)
        sys.exit(1)

    project = resolve_project()
    print_banner(project)

    try:
        client = create_client()
    except Exception as exc:  # noqa: BLE001 - show setup help for any auth/config error
        print(f"\nFailed to connect to Vertex AI: {exc}", file=sys.stderr)
        sys.exit(1)

    chat = client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            temperature=0.7,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not user_input:
            continue

        lowered = user_input.lower()
        if lowered in {"exit", "quit"}:
            print("Bye.")
            break

        if lowered == "clear":
            chat = client.chats.create(
                model=MODEL,
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            print("Chat history cleared.")
            continue

        print("Gemini: ", end="", flush=True)
        try:
            for chunk in chat.send_message_stream(user_input):
                if chunk.text:
                    print(chunk.text, end="", flush=True)
            print()
        except Exception as exc:  # noqa: BLE001
            print(f"\n[error] {exc}", file=sys.stderr)


if __name__ == "__main__":
    run_chat()
