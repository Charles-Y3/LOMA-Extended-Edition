# Bulk import path updates after architecture migration (legacy one-off script)
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPLACEMENTS = [
    ("from core import state", "from services.session import state"),
    ("from core import draft_sync", "from services.session import draft as draft_sync"),
    ("import core.state", "import services.session.state"),
    ("from tools.file_parser import", "from services.file_io import"),
    ("from tools.web_parser import", "from services.web_fetch import"),
    ("from tools.output_generator import", "from capabilities.report_generator.capability_impl import"),
    ("from tools.document_mutator import", "from capabilities.modify_report.capability_impl import"),
    ("from tools.mutation_planner import", "from capabilities.modify_report.mutation_planner import"),
    ("from tools.image_generator import", "from services.image_generation import"),
    ("from core.model_router import", "from services.model_router import"),
    ("from core.profile_pack import", "from pipeline.base.profile_pack import"),
    ("from core.domain import", "from pipeline.schemas.task_schema import"),
    ("from core.handlers import", "from services.session.handlers import"),
    ("from core.general_worker import", "from pipeline.agents.general_agent import"),
    ("from core.vision_worker import", "from pipeline.agents.vision_agent import"),
    ("from core.orchestrator import", "from pipeline.orchestrator import"),
    ("from capabilities.document_mutator.", "from capabilities.modify_report."),
    ("from capabilities.document_writer.", "from capabilities.report_generator."),
    ("from capabilities.file_parser.", "from services.file_io"),
    ("from capabilities.web_parser.", "from services.web_fetch"),
    ("from capabilities.image_processor.", "from services.image_generation"),
    ("from services.image_generation.capability import", "from services.image_generation import"),
]

SKIP = {"_old_code", "scripts", ".git", "__pycache__", "venv"}


def should_skip(p: Path) -> bool:
    return any(s in p.parts for s in SKIP)


for path in ROOT.rglob("*.py"):
    if should_skip(path):
        continue
    text = path.read_text(encoding="utf-8")
    orig = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != orig:
        path.write_text(text, encoding="utf-8")
        print("updated", path.relative_to(ROOT))

print("done")
