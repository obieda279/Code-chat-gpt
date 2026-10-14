from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path=".env"):
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class Settings:
    demo: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    public_url: str = "http://localhost:8080"
    database: str = "data/sentinel.db"
    token: str = ""
    client_id: str = ""
    client_secret: str = ""
    deepl_key: str = ""
    groq_key: str = ""

    @property
    def secure(self):
        return self.public_url.startswith("https://")

    @property
    def callback(self):
        return self.public_url + "/auth/callback"

    def validate(self):
        parsed = urlsplit(self.public_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("PUBLIC_URL must be an origin with no path, credentials, query or fragment")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("PUBLIC_URL must be an HTTP(S) origin")
        if not self.secure and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Public deployments require HTTPS")
        if self.demo and (self.host != "127.0.0.1" or parsed.hostname not in ("localhost", "127.0.0.1")):
            raise ValueError("Demo mode is restricted to the local computer")
        if not self.demo and not all([self.token, self.client_id, self.client_secret]):
            raise ValueError("Set DISCORD_TOKEN, DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET in .env, or run --demo")

    @classmethod
    def from_env(cls, demo=False, port=None):
        load_env()
        actual_port = port or int(os.getenv("PORT", "8080"))
        result = cls(demo=demo, host="127.0.0.1" if demo else os.getenv("HOST", "127.0.0.1"), port=actual_port,
            public_url=f"http://localhost:{actual_port}" if demo else os.getenv("PUBLIC_URL", f"http://localhost:{actual_port}").rstrip("/"),
            database="data/demo.db" if demo else os.getenv("DATABASE_PATH", "data/sentinel.db"),
            token="" if demo else os.getenv("DISCORD_TOKEN", ""), client_id=os.getenv("DISCORD_CLIENT_ID", ""),
            client_secret=os.getenv("DISCORD_CLIENT_SECRET", ""), deepl_key=os.getenv("DEEPL_API_KEY", ""), groq_key=os.getenv("GROQ_API_KEY", ""))
        result.validate()
        return result
