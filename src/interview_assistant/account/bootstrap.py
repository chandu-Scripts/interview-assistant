"""Read public bootstrap settings without opening any personal settings or data."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Bootstrap:
    root: Path
    customer: bool
    url: str = ""
    publishable_key: str = ""

    @classmethod
    def load(cls) -> "Bootstrap":
        root = Path(
            os.environ.get("DATA_DIR")
            or (Path(os.environ.get("APPDATA") or Path.home()) / "InterviewAssistant")
        )
        customer = bool(getattr(sys, "frozen", False)) or "--customer" in sys.argv
        customer = customer or os.environ.get("CHANDOO_MODE") == "customer"
        public: dict[str, str] = {}
        config = Path(__file__).parents[1] / "assets" / "auth.json"
        if config.is_file():
            try:
                raw = json.loads(config.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    public = {k: v for k, v in raw.items() if isinstance(v, str)}
            except (OSError, ValueError):
                pass  # Invalid configuration stays locked; it never enables development mode.
        # A packaged customer binary always uses its bundled project. Environment
        # overrides are for source development and cannot redirect the release gate.
        frozen = bool(getattr(sys, "frozen", False))
        return cls(
            root=root,
            customer=customer,
            url=(
                public.get("url", "")
                if frozen
                else os.environ.get("SUPABASE_URL", public.get("url", ""))
            ).rstrip("/"),
            publishable_key=(
                public.get("publishable_key", "")
                if frozen
                else os.environ.get("SUPABASE_PUBLISHABLE_KEY", public.get("publishable_key", ""))
            ),
        )

    def validate(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".supabase.co")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
            or parsed.port not in (None, 443)
            or not self.publishable_key.startswith("sb_publishable_")
        ):
            raise ValueError("Account service is not configured. Contact the app owner.")
