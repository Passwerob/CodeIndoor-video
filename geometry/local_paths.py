"""Machine-specific inputs of the GPU-side geometry scripts (SAM3, Any4D).

Resolved like the rest of the repo (adsl_adapter/codeindoor_env.py): shell
environment -> runtime/.env. A missing value stops the script with a hint
instead of falling back to someone else's machine path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "adsl_adapter"))
from codeindoor_env import setting  # noqa: E402  (stdlib only)


def required(name: str, what: str) -> str:
    value = setting(name)
    if not value:
        sys.exit(f"{name} is not set: {what} (shell env or runtime/.env, see .env.example)")
    return value


SAM3_CHECKPOINT = required("SAM3_CHECKPOINT", "the SAM3.1 checkpoint, <sam3>/checkpoints/sam3.1/sam3.1_multiplex.pt")
