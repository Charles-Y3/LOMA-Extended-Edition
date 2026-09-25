# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Extended Edition. Adapted from packaging/loma_core.spec (see that
file for the shared infrastructure this one reuses verbatim: chromadb's dynamic-import
hiddenimports, funasr/piper/trafilatura/justext/g2pw/unicode_rbnf non-Python-resource
bundling, pip's on-disk-loader requirement). The real difference from Core Edition is
what this build actually ships:
  - Extended's 8 catalog extensions (config/extension_catalog.json): document_editor,
    chat_archive_manager, knowledge_vault, research, token_tracker, web_viewer,
    history_events, news_brief — no formslator (Core-only; the extensions/formslator
    folder doesn't exist in this repo at all).
  - Extended ships image generation (services/image_generation.py, presentation image
    steps, poster/diagram generation), so the image-gen stack Core's spec explicitly
    EXCLUDES (torchvision, diffusers, peft, gguf, cv2) must be INCLUDED here instead —
    these are real requirements.txt dependencies for this edition, not optional extras.
  - The EAST scene-text-detection model (assets/east_text_detector/, ~92MB) is bundled
    here — Core's spec skips it because Core can't reach the image-generation
    reseed-verify loop that uses it (services/image_text_detection.py) at all.

Build from the project root:
    pyinstaller packaging/loma_extended.spec --noconfirm

Output: dist/LOMA Extended Edition/ (onedir — NOT onefile: onefile self-extracts its
payload on every launch, reintroducing a cold-start delay).

Named "LOMA Extended Edition" per loma_core.spec's own naming-pattern note (LOMA
Complete Edition stays the bare "LOMA" flagship name; Core and Extended each get their
own qualified name to avoid colliding with it or each other).

First-build note: this is this edition's first packaging spec — per loma_core.spec's
own experience, expect one or two follow-up passes once "ModuleNotFoundError" surfaces
at runtime (diffusers/gguf/opencv pull in their own dynamic-import surprises the same
way chromadb did for Core). Rerun with the failing import added to hiddenimports, or as
a `datas` entry if it's a non-Python resource file read by relative path.
"""
from pathlib import Path

import sys

import nicegui
import pip
import trafilatura
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

block_cipher = None
_PROJECT_ROOT = Path(SPECPATH).resolve().parent
_APP_NAME = 'LOMA Extended Edition'

# --- Data files -------------------------------------------------------------
# NiceGUI's static/templates/webcomponents directory MUST be bundled explicitly — this is
# NiceGUI's own documented PyInstaller requirement (see nicegui/scripts/pack.py, the
# `nicegui-pack` helper), not something PyInstaller's static analysis discovers on its own.
_nicegui_dir = str(Path(nicegui.__file__).parent)

# services/pip_runner.py runs pip in-process via runpy for the Model Library's on-demand
# installers. collect_submodules('pip') below only makes PyInstaller aware of pip's .py
# modules for the import graph — it does NOT put a real `pip/` folder on disk, since pure-
# Python modules normally get compiled straight into the PYZ archive instead of extracted
# as loose files. pip needs an actual folder on disk because it resolves its vendored CA
# bundle by real file path at runtime (pip/_vendor/certifi/cacert.pem) rather than through
# the Python import system. Bundling the whole directory as data puts pip on disk for real.
_pip_dir = str(Path(pip.__file__).parent)

# funasr (SenseVoice's runtime) is bundled via collect_all — see loma_core.spec's matching
# comment for the full history (on-demand install needs a real setuptools/distutils/wheel
# toolchain this frozen build never has, which broke funasr's sdist-only dependency jieba).
_funasr_collect = collect_all('funasr')

# trafilatura.settings.use_config() reads settings.cfg relative to its own package
# __file__ at runtime — a non-Python resource Analysis won't bundle on its own.
_trafilatura_cfg = Path(trafilatura.__file__).parent / 'settings.cfg'

# trafilatura 2.x falls back to justext for "unclean" extractions — justext loads its
# stopword lists from justext/stoplists/*.txt at runtime via its own package __file__.
_justext_datas = collect_data_files('justext')

# Piper's espeak-ng-data (per-language phoneme dictionaries) is loaded at runtime relative
# to the piper package's own __file__ — missing it crashes the whole process (native
# extension, not a catchable Python exception) rather than raising cleanly.
_piper_datas = collect_data_files('piper')

# g2pw's 3 lookup-table data files, read straight off disk by path.
_g2pw_datas = collect_data_files('g2pw')

# unicode_rbnf.RbnfEngine.for_language("zh") loads its per-language rule data from
# unicode_rbnf/rbnf/<lang>.xml at runtime — same non-Python-resource class of bug.
_unicode_rbnf_datas = collect_data_files('unicode_rbnf')

datas = [
    (_nicegui_dir, 'nicegui'),
    (str(_PROJECT_ROOT / 'config'), 'config'),
    # Local copy of intfloat/multilingual-e5-small (~470MB) — the embedding model behind
    # Knowledge Vault's Semantic enhancement, Deep search, the chat intent classifier, and
    # (Extended-specific) the explicit-content prompt gate (pipeline/image_safety_embeddings.py).
    # Must stay in sync with resolve_e5_model_path()'s first _LOCAL_CANDIDATES entry
    # (services/rag_embeddings.py) — same relative layout so the frozen build's
    # _PROJECT_ROOT (== _internal/ once frozen) resolves it the same way dev does.
    (str(_PROJECT_ROOT / 'embedding-model'), 'embedding-model'),
    (str(_PROJECT_ROOT / 'ui' / 'assets'), 'ui/assets'),
    # EAST scene-text-detection model — used by the image-generation reseed-verify loop
    # (services/image_text_detection.py) to catch garbled/hallucinated rendered text.
    # Core's spec skips this: Core can't reach the image-generation stack at all.
    (str(_PROJECT_ROOT / 'assets' / 'east_text_detector'), 'assets/east_text_detector'),
    # ExtensionRegistry.discover() reads extension.py/__init__.py off disk (not just via
    # import) to build the registry — without this, every bundled extension silently fails
    # discovery in a packaged build.
    (str(_PROJECT_ROOT / 'extensions'), 'extensions'),
    # Extensions import services.* modules PyInstaller's static Analysis never sees, because
    # it only scans import statements reachable from main.py's own graph — extension.py files
    # are copied as inert data above, not parsed for imports. Bundling services/ as raw files
    # too lets Python's normal filesystem import fall back to disk for anything Analysis missed.
    (str(_PROJECT_ROOT / 'services'), 'services'),
    (str(_PROJECT_ROOT / 'pipeline' / 'templates'), 'pipeline/templates'),
    (str(_PROJECT_ROOT / 'pipeline' / 'skills'), 'pipeline/skills'),
    (_pip_dir, 'pip'),
    # No formslator_default_glossary/formslator_default_styles here — Core-only, the
    # extensions/formslator folder doesn't exist in this repo at all.
]

# Voice-reply (Piper) models are NOT bundled — off by default, picked/downloaded on demand
# instead (setup wizard's optional voice-reply step, or Settings / conversation mode).

if _trafilatura_cfg.is_file():
    datas.append((str(_trafilatura_cfg), 'trafilatura'))

datas += _justext_datas
datas += _piper_datas
datas += _g2pw_datas
datas += _unicode_rbnf_datas

# main.py points SSL_CERT_FILE at certifi's cacert.pem so stdlib urllib HTTPS works in a
# frozen macOS build (which otherwise has no CA bundle); the .pem must be on disk.
datas += collect_data_files('certifi')

binaries = []
datas += _funasr_collect[0]
binaries += _funasr_collect[1]
# torchvision loads its native ops (torchvision/_C, image.so, lib*.dylib/dll) with torch.ops.load_library,
# which PyInstaller's import scan never sees -> "operator torchvision::nms does not exist" on import,
# which hid transformers' image classes and broke every image generation.
_tv_collect = collect_all('torchvision')
binaries += _tv_collect[1]
datas += _tv_collect[0]
hiddenimports_funasr = _funasr_collect[2]

# transformers checks its dependencies' installed versions at runtime via
# importlib.metadata — PyInstaller bundles a package's importable code but NOT its
# .dist-info/METADATA on its own, so those checks raise PackageNotFoundError. Copy the
# metadata explicitly (recursive picks up the whole HF dependency tree).
datas += copy_metadata('transformers', recursive=True)
datas += copy_metadata('tokenizers')
datas += copy_metadata('sentence-transformers', recursive=True)
# diffusers/accelerate/peft do the same importlib.metadata version-check dance — Extended
# actually reaches this code path (Core's spec never needed this, since it excludes the
# whole image-gen stack).
datas += copy_metadata('diffusers', recursive=True)
datas += copy_metadata('accelerate')
datas += copy_metadata('peft')
# transformers 5.x builds each model's lazy export table by REGEX-SCANNING the package's .py SOURCE
# files at runtime (define_import_structure). PyInstaller ships only compiled code, so the table came
# out empty and `from transformers import CLIPImageProcessor` failed. Ship the .py sources of the
# modules we use (top-level + CLIP + auto/utils) as data.
datas += collect_data_files('transformers', include_py_files=True,
                            includes=['*.py', 'utils/*.py', 'models/clip/*.py', 'models/auto/*.py',
                                      'integrations/*.py', 'generation/*.py'])
# transformers decides whether image classes (CLIPImageProcessor, needed by the Stable Diffusion
# pipeline) exist by asking importlib.metadata whether Pillow/torchvision are installed. Pillow is
# an optional extra, so the recursive copy above misses it -> "cannot import name CLIPImageProcessor".
for _pkg in ('pillow', 'torchvision', 'numpy', 'torch', 'safetensors', 'huggingface-hub', 'regex', 'requests', 'tqdm', 'packaging', 'filelock', 'pyyaml'):
    try:
        datas += copy_metadata(_pkg)
    except Exception:
        pass

# requirements.txt: NOT installed into the thin build, but the in-app installers
# (ui/components/asset_downloader.py, capability_installer.py) read it by relative path
# to `pip install -r requirements.txt` as a recovery/reinstall path.
_req_path = _PROJECT_ROOT / 'requirements.txt'
if _req_path.is_file():
    datas.append((str(_req_path), '.'))

# ui/layouts/nav_panel.py reads EDITIONS.md by relative path at runtime (single source of
# truth for the edition-comparison popup's 3 tabs).
_editions_path = _PROJECT_ROOT / 'EDITIONS.md'
if _editions_path.is_file():
    datas.append((str(_editions_path), '.'))

# --- Hidden imports ----------------------------------------------------------
# uvicorn/engineio pick their event-loop and websocket backends dynamically at runtime —
# PyInstaller's static import graph misses these unless listed explicitly.
hiddenimports = [
    'engineio.async_drivers.threading',
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
]

# chromadb.config.get_class() resolves EVERY one of its pluggable component
# implementations by importlib.import_module()-ing a STRING at runtime — none of these are
# ever a real `import chromadb.x.y` statement anywhere in the package, so PyInstaller's
# static Analysis sees none of them.
hiddenimports += [
    'chromadb.api.rust',
    'chromadb_rust_bindings',
    'chromadb.telemetry.product.posthog',
    'chromadb.segment.impl.distributed.segment_directory',
    'chromadb.db.impl.sqlite',
    'chromadb.execution.executor.local',
]

from PyInstaller.utils.hooks import collect_submodules

hiddenimports += collect_submodules('pip')

hiddenimports += [
    'certifi',
    'rank_bm25',
    'msoffcrypto',
]

# tiktoken discovers its encoding plugins (tiktoken_ext.openai_public) by scanning the
# tiktoken_ext namespace package's __path__ with pkgutil.iter_modules — a dynamic scan
# PyInstaller's static import graph can't follow.
hiddenimports += [
    'tiktoken_ext',
    'tiktoken_ext.openai_public',
]

hiddenimports += [
    'faster_whisper',
    'torchaudio',
    'pydub',
    'imageio_ffmpeg',
    'piper',
    'piper.voice',
    'piper.phonemize_chinese',
    'piper.phonemize_espeak',
    'piper.phonemize_hebrew',
    'piper.phonemize_japanese',
    'unicode_rbnf',
    'sentence_stream',
    'g2pw',
]

# Image-generation stack (services/image_generation.py's model-family dispatch) — SD-family
# pipelines load their scheduler/pipeline classes dynamically via diffusers' own
# AutoPipeline-style resolution in some code paths, and gguf's tensor readers are only ever
# reached through image_generation.py's FLUX loader, not a top-level import Analysis
# reliably traces.
hiddenimports += [
    'diffusers',
    'diffusers.pipelines.stable_diffusion',
    'diffusers.pipelines.stable_diffusion_xl',
    'gguf',
]
# transformers and diffusers are lazy-import packages: `from transformers import
# CLIPImageProcessor` resolves to transformers.models.clip.image_processing_clip only at
# runtime, which PyInstaller's static analysis never sees, so the frozen app was missing them and
# every Stable-Diffusion generation failed with "Could not import module 'CLIPImageProcessor'"
# (found by the Mac end-to-end image test; the app then saved the error to a .txt and said
# "Image ready"). Bundle everything the SD pipeline (CLIP text encoder/tokenizer/image
# processor, schedulers, UNet/VAE models, LoRA loaders) pulls in lazily. main.py's
# /loma-selftest route (LOMA_SELFTEST=1) imports these and the build's smoke test fails if not.
hiddenimports += collect_submodules('transformers.models.clip')
hiddenimports += [
    'transformers.image_processing_utils',
    'transformers.image_processing_base',
    'transformers.image_transforms',
    'transformers.image_utils',
    'transformers.processing_utils',
    'transformers.feature_extraction_utils',
]
hiddenimports += collect_submodules('diffusers.schedulers')
hiddenimports += collect_submodules('diffusers.models')
hiddenimports += collect_submodules('diffusers.loaders')
hiddenimports += collect_submodules('diffusers.pipelines.stable_diffusion')
hiddenimports += collect_submodules('diffusers.pipelines.stable_diffusion_xl')

if sys.platform == 'win32':
    hiddenimports += ['win32com.client']

hiddenimports += hiddenimports_funasr
hiddenimports += _tv_collect[2]
# rembg (background removal for image edits) reads pymatting's/onnxruntime's package metadata on import
# and pulls its submodules lazily -> "No package metadata was found for pymatting" when frozen.
datas += copy_metadata('rembg', recursive=True)
_pm_collect = collect_all('pymatting')
datas += _pm_collect[0]
binaries += _pm_collect[1]
hiddenimports += _pm_collect[2]
hiddenimports += collect_submodules('rembg')

# --- Explicit excludes --------------------------------------------------------
excludes = [
    'googleapiclient', 'google_auth_oauthlib', 'google.oauth2',
]
# Deliberately NOT excluded here (unlike loma_core.spec): torchvision, diffusers, peft,
# gguf, cv2 — all real dependencies of this edition's image-generation stack
# (requirements.txt: torchvision, diffusers, peft, gguf, opencv-python-headless).

# UPX breaks many macOS (esp. Apple Silicon) binaries; keep it Windows/Linux-only.
_use_upx = sys.platform != 'darwin'

a = Analysis(
    [str(_PROJECT_ROOT / 'main.py')],
    pathex=[str(_PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# pip must load via a real on-disk SourceFileLoader, not PyInstaller's frozen-archive
# loader — see loma_core.spec's matching comment for the full explanation
# (pip._vendor.distlib.resources.finder() only recognizes disk-based loader types).
a.pure = [entry for entry in a.pure if entry[0] != 'pip' and not entry[0].startswith('pip.')]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=_APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    console=False,  # the splash + browser tab ARE the UI; no console window needed
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_PROJECT_ROOT / 'ui' / 'assets' / 'favicon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=_use_upx,
    upx_exclude=[],
    name=_APP_NAME,
)

# macOS: wrap the onedir tree in "LOMA Extended Edition.app" (Finder-launchable). Windows/
# Linux keep onedir only. BUNDLE's icon must be a real .icns.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=f'{_APP_NAME}.app',
        icon=str(_PROJECT_ROOT / 'ui' / 'assets' / 'favicon.icns'),
        bundle_identifier='local.loma.extended-edition',
        info_plist={
            'NSHighResolutionCapable': True,
            # LOMA has no window of its own (the UI is a browser tab), so macOS treats it as
            # a hidden background app and App Nap would throttle long generations whenever
            # the browser is in front.
            'LSAppNapIsDisabled': True,
            'NSMicrophoneUsageDescription': (
                'LOMA Extended Edition uses the microphone for voice input and dictation.'
            ),
            'CFBundleDisplayName': _APP_NAME,
            'CFBundleName': _APP_NAME,
            'CFBundleShortVersionString': '1.0.1',
        },
    )
