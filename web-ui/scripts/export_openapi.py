import json
from pathlib import Path

from zhsub.web.server import create_app


output_path = Path(__file__).resolve().parents[1] / "openapi.json"
schema = create_app(start_scheduler=False, enforce_readiness=False).openapi()
output_path.write_text(
    json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
