# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — bundles everything Core Edition's 4 shipped extensions
(document_editor, chat_archive_manager, formslator, document_intelligence) need to work
with zero first-run downloads, including Document Intelligence's RAG stack (chromadb,
langchain, sentence-transformers, torch — see requirements.txt). Only genuinely
image-generation-specific packages (diffusers, accelerate, peft, torchvision, torchaudio)
stay excluded, since Core Edition ships no image-output extension (Artwork Studio isn't in
config/extension_catalog.json here) — nothing in this build ever reaches those imports.
Any other optional capability (faster-whisper, SenseVoice, ffmpeg, Playwright's Chromium
binary, GPU torch) is still installed on demand via the Settings -> Model Library
"Optional add-ons" panel (ui/components/model_library_panel.py) or the Extension Library's
enable-time prompt (ui/components/extension_library.py), both backed by
pipeline/gap_handler.py — those installers run in-process via services/pip_runner.py
rather than shelling out to a python.exe this packaged build doesn't have.

Build from the project root:
    pyinstaller packaging/loma_core.spec --noconfirm

Output: dist/LOMA Core Edition/ (onedir — NOT onefile: onefile self-extracts its payload
on every launch, which would reintroduce the exact cold-start delay this app just spent a
round fixing).

Named "LOMA Core Edition" (not bare "LOMA") deliberately — LOMA Complete Edition (the
LOMA1 project) is documented in EDITIONS.md as staying the unqualified flagship name, with
its own exe and AppData folder both bare "LOMA". A future LOMA Extended Edition build
should follow this same file's naming pattern to avoid colliding with either sibling.

First-build note: PyInstaller specs for real apps almost always need one or two follow-up
passes once "ModuleNotFoundError" surfaces at runtime — this is normal, not a broken spec.
Rerun with the failing import added to hiddenimports, or as a `datas` entry if it's a
non-Python resource file main.py reads by relative path.
"""
from pathlib import Path

import sys

import nicegui
import pip
import trafilatura
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

block_cipher = None
_PROJECT_ROOT = Path(SPECPATH).resolve().parent
_APP_NAME = 'LOMA Core Edition'

# --- Data files -------------------------------------------------------------
# NiceGUI's static/templates/webcomponents directory MUST be bundled explicitly — this is
# NiceGUI's own documented PyInstaller requirement (see nicegui/scripts/pack.py, the
# `nicegui-pack` helper), not something PyInstaller's static analysis discovers on its own.
_nicegui_dir = str(Path(nicegui.__file__).parent)

# services/pip_runner.py runs pip in-process via runpy for the Model Library's on-demand
# installers (faster-whisper, SenseVoice, etc). collect_submodules('pip') below only makes
# PyInstaller aware of pip's .py modules for the import graph — it does NOT put a real
# `pip/` folder on disk, since pure-Python modules normally get compiled straight into the
# PYZ archive instead of extracted as loose files. pip needs an actual folder on disk
# because it resolves its vendored CA bundle by real file path at runtime
# (pip/_vendor/certifi/cacert.pem) rather than through the Python import system — with only
# the PYZ copy, that path (relative to a `pip` folder that doesn't exist under _internal/)
# doesn't exist, and any install that needs TLS (Whisper's faster-whisper first download)
# fails with "Could not find a suitable TLS CA certificate bundle". Bundling the whole
# directory as data (same trick as nicegui's own dir above) puts pip on disk for real.
_pip_dir = str(Path(pip.__file__).parent)

# funasr (SenseVoice's runtime) IS bundled here via collect_all, same technique SOTA
# already ships successfully (SOTA.spec's collect_all('funasr')). It was previously
# excluded and pip-installed on demand instead, but that on-demand path requires a real,
# functional setuptools+distutils+wheel toolchain to build funasr's sdist-only dependency
# jieba from source — this frozen build never bundled one (PyInstaller's Analysis only
# picked up a setuptools._vendor fragment, since nothing in LOMA's own code imports
# setuptools directly), so on any account without a pre-cached wheel for jieba, the
# on-demand install fails with "metadata-generation-failed" for jieba. Bundling funasr
# outright sidesteps needing that toolchain at runtime at all.
#
# The prior exclusion's two original concerns are both resolved:
#  1. Double-install risk: _install_sensevoice_sync (ui/components/voice_input_installer.py)
#     now checks `if not _sensevoice_available(): pip_install(...)` before installing, so a
#     bundled funasr is found and skipped rather than reinstalled alongside itself.
#  2. Stale dev-venv PYZ compile: collect_all copies funasr's real package files onto disk
#     (same as any other collect_all'd package) rather than letting Analysis compile a
#     venv copy straight into the PYZ archive, so funasr's `open(... "version.txt")` read
#     in `__init__.py` has real on-disk data next to it — the failure mode that originally
#     motivated the plain exclude, before the size/double-install cost was discovered.
#
# modelscope (funasr's own ModelScope-hub fallback, ~65MB + its own dependency tree —
# aliyunsdkcore, oss2, ...) is NOT pulled in by collect_all('funasr') alone, since it's
# only ever referenced via function-local lazy imports funasr itself never executes for
# this app (SenseVoice always resolves via a local path or Hugging Face first, same as
# SOTA's get_sensevoice_model — see services/voice_input.py). SOTA's own real build
# confirms this in practice: modelscope's package is absent from its frozen dist despite
# collect_all('funasr') with zero explicit excludes. numba/llvmlite (~115MB, funasr's own
# JIT-accelerated audio/feature code, unrelated to modelscope) DO come along regardless —
# confirmed present in SOTA's real shipped build too, so budget ~150MB total for this
# collect_all, not the ~50-80MB the old funasr-only estimate assumed.
_funasr_collect = collect_all('funasr')

# trafilatura.settings.use_config() reads settings.cfg relative to its own package
# __file__ at runtime — PyInstaller's static Analysis only bundles the .py modules it
# imports, not this non-Python resource, so a packaged build silently loads an empty
# config and every extract() call fails with "No option 'min_extracted_size' in
# section: 'DEFAULT'" (grounded/web-search chat). Confirmed missing from datas below.
_trafilatura_cfg = Path(trafilatura.__file__).parent / 'settings.cfg'

# trafilatura 2.x falls back to justext for "unclean" extractions (exactly the heavy,
# ad-laden news sites this app's own web_fetch.py comments call out, e.g. abc.net.au) —
# justext loads its stopword lists from justext/stoplists/*.txt at runtime via its own
# package __file__, another non-Python resource Analysis won't bundle on its own. Missing
# it doesn't crash cleanly: it raises past trafilatura's own error handling, so web_fetch.py
# ends up returning the raw exception text as if it were the scraped page content — which
# then got fed straight to the LLM as "page content" (see context_builder.py fix). Same bug
# class as the trafilatura settings.cfg fix above, just one dependency deeper.
_justext_datas = collect_data_files('justext')

# Piper's espeak-ng-data (per-language phoneme dictionaries, ~470 files) is loaded at
# runtime relative to the piper package's own __file__ — same non-Python-resource class of
# bug as trafilatura/justext above, PyInstaller's Analysis won't bundle it on its own. But
# unlike those two, the lookup happens inside espeakbridge.pyd (a compiled native
# extension), so a missing directory in the frozen build doesn't raise a catchable Python
# exception the way a missing .cfg/.txt does — it crashes the whole process, which reads to
# the user as "connection lost, try to reconnect..." mid-reply instead of a normal error.
# Confirmed: Chinese Piper voices reproduced this after being downloaded via the Model
# Library and selected as the reply voice, while the SAPI system voice (no piper/espeak
# involved) worked fine.
_piper_datas = collect_data_files('piper')

# g2pw's 3 lookup-table data files (see the 'g2pw' hiddenimport note below) — g2pw_onnx.py
# reads them straight off disk by path, same non-Python-resource class of bug as funasr
# above, PyInstaller's Analysis won't bundle them on its own.
_g2pw_datas = collect_data_files('g2pw')

# unicode_rbnf.RbnfEngine.for_language("zh") (piper.phonemize_chinese's number-to-words
# step for xiao_ya) loads its per-language rule data from unicode_rbnf/rbnf/<lang>.xml at
# runtime — same non-Python-resource class of bug as everything else in this section.
# Missing this doesn't raise a file-not-found error though: it raises ValueError("zh is
# not supported") instead, since RbnfEngine can only report the languages whose data files
# it actually found on disk — confirmed via isolated repro (worked in dev venv with the
# real installed package on disk, failed the exact same way in a frozen build without this).
_unicode_rbnf_datas = collect_data_files('unicode_rbnf')

datas = [
    (_nicegui_dir, 'nicegui'),
    (str(_PROJECT_ROOT / 'config'), 'config'),
    # Local copy of intfloat/multilingual-e5-small (~470MB) — the embedding model behind
    # Document Intelligence's Semantic enhancement, Deep search, and the chat intent
    # classifier (services/rag_embeddings.py, pipeline/intent_embeddings.py). Without this,
    # resolve_e5_model_path() falls back to a bare HF Hub id and the first embed call blocks
    # on a live multi-hundred-MB-to-multi-GB download with no timeout — reproduced as a
    # Semantic-enhancement build that silently "hung" for 5+ minutes on just 2 small files,
    # and (separately) as an unrecoverable process crash class documented at _piper_datas
    # below. Bundling it here means every included extension actually works with zero
    # first-run download, per the project's own "no additional download" requirement for
    # shipped extensions. Must stay in sync with resolve_e5_model_path()'s first
    # _LOCAL_CANDIDATES entry (services/rag_embeddings.py) — same relative layout
    # (`embedding-model/` next to this project's other top-level folders) so the frozen
    # build's _PROJECT_ROOT (== _internal/ once frozen) resolves it the same way dev does.
    (str(_PROJECT_ROOT / 'embedding-model'), 'embedding-model'),
    (str(_PROJECT_ROOT / 'ui' / 'assets'), 'ui/assets'),
    # ExtensionRegistry.discover() reads extension.py/__init__.py off disk (not just via
    # import) to build the registry — without this, every bundled extension silently fails
    # discovery in a packaged build (confirmed: all 16 missing on first packaged run).
    (str(_PROJECT_ROOT / 'extensions'), 'extensions'),
    # Extensions import services.* modules PyInstaller's static Analysis never sees, because
    # it only scans import statements reachable from main.py's own graph — extension.py files
    # are copied as inert data above, not parsed for imports. Bundling services/ as raw files
    # too (same trick as extensions/ itself) lets Python's normal filesystem import fall back
    # to disk for anything Analysis missed (confirmed: artwork_studio's `from
    # services.session.chat_media import ...` 404'd with services/ absent from datas).
    (str(_PROJECT_ROOT / 'services'), 'services'),
    (str(_PROJECT_ROOT / 'pipeline' / 'templates'), 'pipeline/templates'),
    (str(_PROJECT_ROOT / 'pipeline' / 'skills'), 'pipeline/skills'),
    (_pip_dir, 'pip'),
    # Formslator's two default style templates (services/formslator/worker.py's
    # _resolve_template/_resolve_single_column_template already assume these filenames
    # exist and auto-select them when nothing else has been chosen) — bundled as a
    # read-only resource here, then copied into the writable STYLES_DIR on first run by
    # services/formslator/paths.py's ensure_default_templates() (called from main.py).
    # Not sourced from data/formslator/styles/ directly: that whole tree is gitignored
    # (data/*), so a checkout-based build (e.g. GitHub Actions) would find it empty.
    (str(_PROJECT_ROOT / 'packaging' / 'formslator_default_styles'), 'formslator_default_styles'),
    # Formslator's bundled default glossary (TaoTerms) + its column configuration — same
    # gitignored-data/ workaround as formslator_default_styles above, copied into the
    # writable GLOSSARY_DIR on first run by services/formslator/paths.py's
    # ensure_default_glossary() (called from main.py).
    (str(_PROJECT_ROOT / 'packaging' / 'formslator_default_glossary'), 'formslator_default_glossary'),
]

# Voice-reply (Piper) models are NOT bundled — voice reply is off by default and not a
# documented Core Edition capability, so shipping ~60MB+ per language in every install
# isn't worth it for users who never turn it on. Picked/downloaded on demand instead: in
# the setup wizard's optional voice-reply step, or later via Settings / conversation mode
# (see ui/components/setup_wizard.py, services/tts_engines.py PIPER_VOICE_CATALOG).

if _trafilatura_cfg.is_file():
    datas.append((str(_trafilatura_cfg), 'trafilatura'))

datas += _justext_datas
datas += _piper_datas
datas += _g2pw_datas
datas += _unicode_rbnf_datas

binaries = []
datas += _funasr_collect[0]
binaries += _funasr_collect[1]
hiddenimports_funasr = _funasr_collect[2]

# transformers checks its dependencies' installed versions at runtime via
# importlib.metadata (transformers.utils.versions.require_version -> importlib.metadata
# .version(pkg)). PyInstaller bundles a package's importable code but NOT its
# .dist-info/METADATA on its own, so those checks raise PackageNotFoundError, which
# transformers rewraps as e.g. "The 'tokenizers>=0.22.0,<=0.23.0' distribution was not
# found and is required by this application" — which is exactly what Document
# Intelligence's Semantic enhancement hit (its first `import sentence_transformers` pulls
# in transformers, which runs that check) in the packaged .exe. The contrib
# hook-transformers is supposed to copy this metadata but did not cover tokenizers/
# transformers here, so copy it explicitly (recursive picks up the whole HF dependency
# tree — huggingface-hub, safetensors, numpy, regex, ...).
datas += copy_metadata('transformers', recursive=True)
datas += copy_metadata('tokenizers')
datas += copy_metadata('sentence-transformers', recursive=True)

# requirements.txt: NOT installed into the thin build, but the in-app installers
# (ui/components/asset_downloader.py, capability_installer.py) read it by relative path
# via services.platform_paths.resource_root() to `pip install -r requirements.txt` as a
# recovery/reinstall path (image generation, RAG, Document Intelligence deps all live
# there now). Without bundling the file itself, every "Optional add-ons" button in a
# packaged build points at a path that doesn't exist.
_req_path = _PROJECT_ROOT / 'requirements.txt'
if _req_path.is_file():
    datas.append((str(_req_path), '.'))

# ui/layouts/nav_panel.py reads EDITIONS.md by relative path at runtime (single source of
# truth for the edition-comparison popup's 3 tabs) — without bundling it, that read fails
# silently in a packaged build and the popup falls back to its no-tabs view.
_editions_path = _PROJECT_ROOT / 'EDITIONS.md'
if _editions_path.is_file():
    datas.append((str(_editions_path), '.'))

# EAST scene-text-detection model (services/image_text_detection.py, assets/
# east_text_detector/) is deliberately NOT added here — it's only used by the image-
# generation reseed-verify loop, and this spec is Core Edition, which excludes the
# whole image-generation stack below (diffusers/peft/torchvision) since Core ships no
# image-output extension at all. Bundling a ~92MB model for a feature this build can't
# reach would be pure waste. When an Extended/Complete Edition packaging spec exists,
# the bundling entry (source path -> 'assets/east_text_detector') belongs there instead —
# for now, running Extended Edition from source (this repo's normal dev/current mode)
# already resolves the file correctly via services.platform_paths.resource_root()
# falling back to the repo root when not frozen.

# LICENSE.txt and README.txt are copied into dist/ post-build by build.bat instead of
# listed here — packaging/DIST_README.txt needs renaming to README.txt on the way out,
# which PyInstaller's `datas` (source path -> dest DIRECTORY, not filename) can't do.

# --- Hidden imports ----------------------------------------------------------
# uvicorn/engineio pick their event-loop and websocket backends dynamically at runtime
# (the "auto" modules below) — PyInstaller's static import graph misses these unless
# listed explicitly. This is a well-known uvicorn+PyInstaller gotcha, independent of LOMA.
hiddenimports = [
    'engineio.async_drivers.threading',
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan.on',
]

# chromadb.config.get_class() resolves EVERY one of its pluggable component
# implementations (chroma_api_impl, chroma_product_telemetry_impl, chroma_sysdb_impl, ...)
# by importlib.import_module()-ing a STRING at runtime (chromadb/config.py) — none of these
# are ever a real `import chromadb.x.y` statement anywhere in the package, so PyInstaller's
# static Analysis sees none of them. Confirmed via direct isolated repro: fixing
# chromadb.api.rust alone (the first one Chroma() touches) just surfaces the NEXT one
# ("No module named 'chromadb.telemetry.product.posthog'") on the next call — this is every
# *_impl default in chromadb/config.py in one pass instead of chasing them one at a time.
# chromadb_rust_bindings is chromadb.api.rust's own dependency (a compiled .pyd).
hiddenimports += [
    'chromadb.api.rust',
    'chromadb_rust_bindings',
    'chromadb.telemetry.product.posthog',
    'chromadb.segment.impl.distributed.segment_directory',
    'chromadb.db.impl.sqlite',
    'chromadb.execution.executor.local',
]

# services/pip_runner.py runs `pip`/`playwright` in-process via runpy (see that module's
# docstring for why: a packaged .exe has no standalone python.exe to shell out to). pip is
# only ever reached dynamically through runpy, so PyInstaller's static import graph misses
# it unless collected explicitly.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports += collect_submodules('pip')

# Document Intelligence's on-demand-install dependencies (lexical + encrypted-office core,
# plus its Deep search RAG stack) are bundled unconditionally now — see requirements.txt —
# so the extension "just works" out of the box instead of needing a first-run download.
# rank_bm25/msoffcrypto are only ever imported lazily inside functions (pipeline/gap_handler.py
# _doc_intel_ready()), which Analysis' bytecode scan should already catch, but list them
# explicitly as a safety net against that scan missing a dynamic import path.
hiddenimports += [
    'rank_bm25',
    'msoffcrypto',
]

# tiktoken discovers its encoding plugins (tiktoken_ext.openai_public) by scanning the
# tiktoken_ext namespace package's __path__ with pkgutil.iter_modules — a dynamic scan
# PyInstaller's static import graph can't follow. Without this, get_encoding() finds zero
# plugins in the frozen exe ("Unknown encoding ...", used by Document Intelligence's
# chunker in extensions/document_intelligence/extract.py, which now also has a
# non-tiktoken fallback so ingestion never hard-fails on this).
hiddenimports += [
    'tiktoken_ext',
    'tiktoken_ext.openai_public',
]

# Sound/video transcription (faster-whisper) and offline voice reply (pydub +
# imageio-ffmpeg) are bundled unconditionally — same lazy-import-inside-a-function pattern
# (services/media_transcription.py, services/tts_engines.py) that needs a safety net here.
# funasr/SenseVoice is deliberately NOT in this list — see the "funasr (SenseVoice's
# runtime) is deliberately NOT bundled" comment near the top of this file for why; it's
# installed on demand instead (ui/components/voice_input_installer.py's
# _install_sensevoice_sync), same as its model weights already were.
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
    # zh_CN-xiao_ya-medium is the one Piper voice using pinyin/g2pW phonemization instead
    # of espeak (huayan/chaowen) — piper.phonemize_chinese imports these directly at
    # module level, so without them `import piper.phonemize_chinese` itself fails with
    # ModuleNotFoundError. Confirmed via direct reproduction: that ModuleNotFoundError gets
    # masked by a `with wave.open(...)` block's own cleanup exception ("# channels not
    # specified") by the time it reaches _synthesize_piper's caller, showing up as a
    # baffling, unrelated-looking error with zero clue about the real missing-package cause.
    'unicode_rbnf',
    'sentence_stream',
    # g2pw_onnx.py deliberately avoids `import g2pw` (g2pw/__init__.py pulls in torch) and
    # instead locates it via importlib.util.find_spec('g2pw') purely to read 3 lookup-table
    # data files (bopomofo_to_pinyin_wo_tune_dict.json, char_bopomofo_dict.json,
    # bert-base-chinese_s2t_dict.txt) straight off disk by path — hiddenimport makes the
    # package (and thus find_spec) resolvable in the frozen build; collect_data_files below
    # bundles the actual files, since Analysis only bundles .py modules on its own.
    'g2pw',
]
if sys.platform == 'win32':
    hiddenimports += ['win32com.client']

hiddenimports += hiddenimports_funasr

# --- Explicit excludes --------------------------------------------------------
# Image-generation-only heavy stack — deliberately NOT bundled: Core Edition ships no
# image-output extension (Artwork Studio isn't in config/extension_catalog.json here), so
# nothing in this build ever reaches these imports. torchvision/torchaudio are image/audio-
# generation-specific and excluded for the same reason; plain `torch` and `transformers`
# stay OUT of this list because Document Intelligence's Deep search (RAG) needs them
# transitively via sentence-transformers/langchain-huggingface — see requirements.txt.
#
# funasr is deliberately NOT in this list anymore — see the collect_all('funasr') note
# near the top of this file for the current bundling approach and why the previous
# exclude+on-demand-install combination was reverted.
excludes = [
    'torchvision',
    'diffusers', 'peft',
    # gguf: only imported by image_generation.py's FLUX loader. cv2/opencv: only
    # imported by image_text_detection.py, itself only reachable from the image-
    # generation reseed-verify loop — no other part of this codebase touches either.
    'gguf',
    'cv2',
    'googleapiclient', 'google_auth_oauthlib', 'google.oauth2',
]
# 'torchaudio' and 'accelerate' were previously also excluded here as "image-generation-
# only" — wrong for both: torchaudio is a hard dependency of faster-whisper/funasr speech
# transcription (already force-included via hiddenimports below, which happened to paper
# over the exclude), and accelerate is used by transformers/sentence-transformers' own
# from_pretrained() machinery (transformers/integrations/accelerate.py) for the E5
# embedding model behind Document Intelligence's Semantic enhancement — excluding it
# reproduced as "Semantic enhancement failed: No module named 'accelerate'" the moment a
# real semantic build was attempted in a packaged .exe (confirmed; the earlier chromadb-
# only fix wasn't the whole story). Neither belongs in an "image-generation-only" list.

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
# loader — pip._vendor.distlib.resources.finder() (used to build console-script launchers
# during on-demand installs, e.g. faster-whisper) only recognizes disk-based loader types
# (SourceFileLoader/FileFinder/zipimporter) in its _finder_registry. collect_submodules('pip')
# above makes Analysis compile pip's .py files straight into the PYZ archive, whose frozen
# loader isn't registered there, so `finder('pip._vendor.distlib')` raises
# "Unable to locate finder for 'pip._vendor.distlib'" the moment pip needs it. Stripping pip
# out of a.pure here forces `import pip` to fall back to the physical pip/ folder already
# bundled via _pip_dir in datas above (sys._MEIPASS is on sys.path in a frozen build), which
# has a normal on-disk loader distlib does recognize.
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

# macOS: wrap the onedir tree in "LOMA Core Edition.app" (Finder-launchable). Windows/Linux
# keep onedir only. BUNDLE's icon must be a real .icns — a Windows .ico here is silently
# ignored by Finder/Dock, which fall back to the generic app icon. See ui/assets/favicon.icns.
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name=f'{_APP_NAME}.app',
        icon=str(_PROJECT_ROOT / 'ui' / 'assets' / 'favicon.icns'),
        bundle_identifier='local.loma.core-edition',
        info_plist={
            'NSHighResolutionCapable': True,
            'NSMicrophoneUsageDescription': (
                'LOMA Core Edition uses the microphone for voice input and dictation.'
            ),
            'CFBundleDisplayName': _APP_NAME,
            'CFBundleName': _APP_NAME,
            'CFBundleShortVersionString': '1.0.0',
        },
    )
