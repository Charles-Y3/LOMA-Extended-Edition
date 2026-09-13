# One-off migration helper
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

tool_dirs = {
    "file_parser": "file_parser.py",
    "web_parser": "web_parser.py",
    "document_writer": "output_generator.py",
    "document_mutator": "document_mutator.py",
    "image_processor": "image_generator.py",
}
for folder, fname in tool_dirs.items():
    d = ROOT / "capabilities" / folder
    d.mkdir(parents=True, exist_ok=True)
    src = ROOT / "tools" / fname
    if src.exists():
        shutil.copy2(src, d / "capability.py")
        (d / "__init__.py").write_text(
            f"from capabilities.{folder}.capability import *\n", encoding="utf-8"
        )

for s in ["inference", "filewatcher", "sandbox", "session", "logging"]:
    (ROOT / "services" / s).mkdir(parents=True, exist_ok=True)
    (ROOT / "services" / s / "__init__.py").write_text("", encoding="utf-8")

pairs = [
    ("services/ollama_client.py", "services/inference/ollama_client.py"),
    ("services/chat_archive.py", "services/session/chat_archive.py"),
    ("core/state.py", "services/session/state.py"),
    ("core/model_router.py", "services/inference/model_router.py"),
    ("core/draft_sync.py", "services/session/draft.py"),
    ("core/general_worker.py", "core/agents/general_agent.py"),
    ("core/vision_worker.py", "core/agents/vision_agent.py"),
    ("core/profile_pack.py", "core/base/profile_pack.py"),
    ("ui/components/profile_manager_ui.py", "extensions/profile_manager/extension.py"),
    ("ui/components/chat_archive.py", "extensions/chat_archive_manager/extension.py"),
]
for a, b in pairs:
    src, dst = ROOT / a, ROOT / b
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

print("migration copy done")
