from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REMOVE = [
    "config.py",
    "tools",
    "prompts",
    "core/state.py",
    "core/handlers.py",
    "core/engine.py",
    "core/domain.py",
    "core/context.py",
    "core/events.py",
    "core/model_router.py",
    "core/general_worker.py",
    "core/vision_worker.py",
    "core/coder_worker.py",
    "core/verifier_worker.py",
    "core/profile_pack.py",
    "core/draft_sync.py",
    "core/draft_revision.py",
    "core/artifact_compiler.py",
    "core/preview_revision.py",
    "core/preview_selection.py",
    "services/profile_manager.py",
    "services/chat_archive.py",
    "services/ollama_client.py",
    "ui/components/profile_manager_ui.py",
    "ui/components/chat_archive.py",
]

for rel in REMOVE:
    p = ROOT / rel
    if p.is_dir():
        import shutil
        shutil.rmtree(p, ignore_errors=True)
        print("removed dir", rel)
    elif p.exists():
        p.unlink()
        print("removed", rel)

print("cleanup done")
