# -*- coding: utf-8 -*-
"""Localized model-catalog descriptions and setup-wizard subsystem rows
(merged into pipeline.i18n.TRANSLATIONS via i18n_extensions.merge_into).

Descriptions are looked up by model name — see services/catalog_i18n.py — and fall back to the
English text in config/model_catalog.py, so a model added later still shows its description."""
from __future__ import annotations

import re


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


# name -> (en, zh_tw, zh_cn, es, de)
_DESC: dict[str, tuple[str, str, str, str, str]] = {
    "sorc/qwen3.5-instruct:2b": (
        "Compact vision-language model for routing and quick tasks.",
        "精巧的視覺語言模型，適合任務分派與快速任務。",
        "精巧的视觉语言模型，适合任务分派与快速任务。",
        "Modelo compacto de visión y lenguaje para enrutamiento y tareas rápidas.",
        "Kompaktes Vision-Sprachmodell für Routing und schnelle Aufgaben.",
    ),
    "llava-phi3:3.8b": (
        "Balanced multimodal assistant for everyday chat and vision.",
        "均衡的多模態助理，適合日常對話與視覺任務。",
        "均衡的多模态助手，适合日常对话与视觉任务。",
        "Asistente multimodal equilibrado para el chat diario y la visión.",
        "Ausgewogener multimodaler Assistent für Alltagschat und Bildverständnis.",
    ),
    "frob/qwen3.5-instruct:4b": (
        "Strongest curated option for 8 GB class machines.",
        "適用於 8 GB 等級電腦的最強精選選項。",
        "适用于 8 GB 级别电脑的最强精选选项。",
        "La mejor opción seleccionada para equipos de clase 8 GB.",
        "Stärkste kuratierte Option für Rechner der 8-GB-Klasse.",
    ),
    "gemma4:e2b": (
        "Efficient Gemma 4 vision stack for tier-1 hardware.",
        "適用於第一級硬體的高效 Gemma 4 視覺模型。",
        "适用于第一级硬件的高效 Gemma 4 视觉模型。",
        "Pila de visión Gemma 4 eficiente para hardware de nivel 1.",
        "Effizienter Gemma-4-Vision-Stack für Hardware der Stufe 1.",
    ),
    "llava:7b": (
        "Light LLaVA variant when RAM is tight.",
        "記憶體吃緊時使用的輕量版 LLaVA。",
        "内存紧张时使用的轻量版 LLaVA。",
        "Variante ligera de LLaVA cuando la RAM es limitada.",
        "Leichte LLaVA-Variante bei knappem Arbeitsspeicher.",
    ),
    "minicpm-v:8b": (
        "Strong document and scene understanding.",
        "強大的文件與場景理解能力。",
        "强大的文档与场景理解能力。",
        "Sólida comprensión de documentos y escenas.",
        "Starkes Verständnis von Dokumenten und Szenen.",
    ),
    "frob/qwen3.5-instruct:9b": (
        "Mid-size multimodal model for agentic workflows.",
        "中型多模態模型，適合代理式工作流程。",
        "中型多模态模型，适合代理式工作流程。",
        "Modelo multimodal de tamaño medio para flujos de trabajo con agentes.",
        "Mittelgroßes multimodales Modell für agentische Workflows.",
    ),
    "gemma4:12b": (
        "Excellent OCR and layout understanding.",
        "出色的 OCR 與版面理解能力。",
        "出色的 OCR 与版面理解能力。",
        "Excelente OCR y comprensión de diseño.",
        "Hervorragende OCR und Layout-Erkennung.",
    ),
    "llama3.2-vision:11b": (
        "Deep visual reasoning on mid-range hardware.",
        "在中階硬體上提供深入的視覺推理。",
        "在中端硬件上提供深入的视觉推理。",
        "Razonamiento visual profundo en hardware de gama media.",
        "Tiefes visuelles Denken auf Mittelklasse-Hardware.",
    ),
    "llava:13b": (
        "Larger LLaVA when free RAM allows.",
        "可用記憶體充足時使用的較大版 LLaVA。",
        "可用内存充足时使用的较大版 LLaVA。",
        "LLaVA más grande cuando la RAM libre lo permite.",
        "Größeres LLaVA, wenn genug freier Arbeitsspeicher vorhanden ist.",
    ),
    "haervwe/GLM-4.6V-Flash-9B:latest": (
        "Fast GLM vision model for complex scenes.",
        "適合複雜場景的快速 GLM 視覺模型。",
        "适合复杂场景的快速 GLM 视觉模型。",
        "Modelo de visión GLM rápido para escenas complejas.",
        "Schnelles GLM-Vision-Modell für komplexe Szenen.",
    ),
    "gemma4:e4b": (
        "Largest Gemma 4 variant for 16 GB class machines.",
        "適用於 16 GB 等級電腦的最大 Gemma 4 版本。",
        "适用于 16 GB 级别电脑的最大 Gemma 4 版本。",
        "La variante más grande de Gemma 4 para equipos de clase 16 GB.",
        "Größte Gemma-4-Variante für Rechner der 16-GB-Klasse.",
    ),
    "mistral-small3.2:24b": (
        "Entry point for high-RAM orchestration.",
        "高記憶體協調運作的入門選擇。",
        "高内存协调运作的入门选择。",
        "Punto de entrada para la orquestación con mucha RAM.",
        "Einstieg in die Orchestrierung mit viel Arbeitsspeicher.",
    ),
    "gemma4:26b": (
        "Top-tier multimodal reasoning for capable PCs.",
        "適合高效能電腦的頂級多模態推理。",
        "适合高性能电脑的顶级多模态推理。",
        "Razonamiento multimodal de primer nivel para PC potentes.",
        "Erstklassiges multimodales Denken für leistungsfähige PCs.",
    ),
    "frob/qwen3.5-instruct:35b": (
        "Heavy multimodal model for coding and verification.",
        "大型多模態模型，適合程式設計與驗證。",
        "大型多模态模型，适合编程与验证。",
        "Modelo multimodal pesado para programación y verificación.",
        "Schweres multimodales Modell für Programmierung und Verifikation.",
    ),
    "nemotron3:33b": (
        "Maximum curated intelligence for planning and decomposition.",
        "適合規劃與任務拆解的最高階精選智慧。",
        "适合规划与任务拆解的最高阶精选智能。",
        "Máxima inteligencia seleccionada para planificación y descomposición.",
        "Maximale kuratierte Intelligenz für Planung und Aufgabenzerlegung.",
    ),
    "SG161222/Realistic_Vision_V6.0_B1_noVAE": (
        "SD 1.5 checkpoint tuned for photorealism. Low = LCM LoRA, 8 steps. High = DPM++ SDE Karras sampler, ~28 steps, no LCM.",
        "針對擬真寫實調校的 SD 1.5 檢查點。低品質＝LCM LoRA，8 步；高品質＝DPM++ SDE Karras 取樣器，約 28 步，不使用 LCM。",
        "针对拟真写实调校的 SD 1.5 检查点。低质量＝LCM LoRA，8 步；高质量＝DPM++ SDE Karras 采样器，约 28 步，不使用 LCM。",
        "Checkpoint SD 1.5 ajustado para fotorrealismo. Bajo = LCM LoRA, 8 pasos. Alto = muestreador DPM++ SDE Karras, ~28 pasos, sin LCM.",
        "SD-1.5-Checkpoint für Fotorealismus. Niedrig = LCM LoRA, 8 Schritte. Hoch = DPM++-SDE-Karras-Sampler, ~28 Schritte, kein LCM.",
    ),
    "Lykon/dreamshaper-8": (
        "Versatile SD 1.5 checkpoint. Low = LCM LoRA, 8 steps. High = DPM++ SDE Karras sampler, ~28 steps, no LCM.",
        "用途廣泛的 SD 1.5 檢查點。低品質＝LCM LoRA，8 步；高品質＝DPM++ SDE Karras 取樣器，約 28 步，不使用 LCM。",
        "用途广泛的 SD 1.5 检查点。低质量＝LCM LoRA，8 步；高质量＝DPM++ SDE Karras 采样器，约 28 步，不使用 LCM。",
        "Checkpoint SD 1.5 versátil. Bajo = LCM LoRA, 8 pasos. Alto = muestreador DPM++ SDE Karras, ~28 pasos, sin LCM.",
        "Vielseitiger SD-1.5-Checkpoint. Niedrig = LCM LoRA, 8 Schritte. Hoch = DPM++-SDE-Karras-Sampler, ~28 Schritte, kein LCM.",
    ),
    "ByteDance/SDXL-Lightning": (
        "Distilled SDXL checkpoint — near-base-SDXL 1024px quality, 8 steps.",
        "蒸餾版 SDXL 檢查點——接近原版 SDXL 的 1024px 畫質，僅需 8 步。",
        "蒸馏版 SDXL 检查点——接近原版 SDXL 的 1024px 画质，仅需 8 步。",
        "Checkpoint SDXL destilado: calidad casi de SDXL base a 1024 px, 8 pasos.",
        "Destillierter SDXL-Checkpoint — nahezu SDXL-Basisqualität bei 1024 px, 8 Schritte.",
    ),
    "black-forest-labs/FLUX.2-klein-4B": (
        "DiT architecture, distilled (4 steps, guidance-distilled) — No Low/High mode: a single fixed quality setting. 4-bit quantised model. Best image generator but slowest in LOMA Extended Edition.",
        "DiT 架構、經蒸餾（4 步、引導蒸餾）——沒有低／高品質模式，僅有單一固定品質。4 位元量化模型。是最佳的圖像生成器，但在 LOMA 擴充版中速度最慢。",
        "DiT 架构、经蒸馏（4 步、引导蒸馏）——没有低/高质量模式，仅有单一固定质量。4 位量化模型。是最佳的图像生成器，但在 LOMA 扩展版中速度最慢。",
        "Arquitectura DiT, destilado (4 pasos, destilado con guía): sin modo Bajo/Alto, un único ajuste de calidad fijo. Modelo cuantizado a 4 bits. El mejor generador de imágenes, pero el más lento en LOMA Extended Edition.",
        "DiT-Architektur, destilliert (4 Schritte, Guidance-destilliert) — kein Niedrig/Hoch-Modus, nur eine feste Qualitätsstufe. 4-Bit-quantisiertes Modell. Bester Bildgenerator, aber der langsamste in LOMA Extended Edition.",
    ),
    "vision-llm": (
        "Scanned PDF OCR uses your installed vision LLM.",
        "掃描版 PDF 的 OCR 會使用您已安裝的視覺模型。",
        "扫描版 PDF 的 OCR 会使用您已安装的视觉模型。",
        "El OCR de PDF escaneados usa el modelo de visión que tienes instalado.",
        "Die OCR gescannter PDFs nutzt dein installiertes Vision-Modell.",
    ),
    "hybrid-whisper": (
        "faster-whisper tiny preview + accurate model for live mic/dictate.",
        "faster-whisper tiny 即時預覽＋精準模型，用於麥克風即時輸入／聽寫。",
        "faster-whisper tiny 实时预览＋精准模型，用于麦克风实时输入/听写。",
        "Vista previa faster-whisper tiny + modelo preciso para micrófono/dictado en vivo.",
        "faster-whisper-tiny-Vorschau + genaues Modell für Live-Mikrofon/Diktat.",
    ),
    "sensevoice": (
        "FunASR SenseVoiceSmall for English and Chinese dictation.",
        "FunASR SenseVoiceSmall，適用於英文與中文聽寫。",
        "FunASR SenseVoiceSmall，适用于英文与中文听写。",
        "FunASR SenseVoiceSmall para dictado en inglés y chino.",
        "FunASR SenseVoiceSmall für Diktat auf Englisch und Chinesisch.",
    ),
    "video-frame-extract": (
        "Video frames use your installed vision LLM.",
        "影片畫面會使用您已安裝的視覺模型。",
        "视频画面会使用您已安装的视觉模型。",
        "Los fotogramas de video usan el modelo de visión que tienes instalado.",
        "Videobilder nutzen dein installiertes Vision-Modell.",
    ),
    "local-tts": (
        "Neural TTS — currently a placeholder tone generator only.",
        "神經網路語音合成——目前僅為佔位用的音調產生器。",
        "神经网络语音合成——目前仅为占位用的音调生成器。",
        "TTS neuronal: por ahora solo un generador de tonos de marcador de posición.",
        "Neuronale Sprachsynthese — derzeit nur ein Platzhalter-Tongenerator.",
    ),
    "local-video-gen": (
        "Video generation — currently a placeholder metadata export only.",
        "影片生成——目前僅為佔位用的中繼資料匯出。",
        "视频生成——目前仅为占位用的元数据导出。",
        "Generación de video: por ahora solo una exportación de metadatos de marcador de posición.",
        "Videoerzeugung — derzeit nur ein Platzhalter-Metadatenexport.",
    ),
}

_SUBSYSTEM: dict[str, tuple[str, str, str, str, str]] = {
    "subsystem.image_generation": ("Image generation", "圖像生成", "图像生成", "Generación de imágenes", "Bilderzeugung"),
    "subsystem.ocr": ("OCR (scanned documents)", "OCR（掃描文件）", "OCR（扫描文档）", "OCR (documentos escaneados)", "OCR (gescannte Dokumente)"),
    "subsystem.voice_input": ("Voice input", "語音輸入", "语音输入", "Entrada de voz", "Spracheingabe"),
    "subsystem.video_input": ("Video input", "影片輸入", "视频输入", "Entrada de video", "Videoeingabe"),
    "subsystem.audio_generation": ("Sound generation", "音效生成", "音效生成", "Generación de sonido", "Klangerzeugung"),
    "subsystem.video_generation": ("Video generation", "影片生成", "视频生成", "Generación de video", "Videoerzeugung"),
    "subsystem.status_ready": ("Ready", "已就緒", "已就绪", "Listo", "Bereit"),
    "subsystem.status_missing": ("Missing", "未安裝", "未安装", "Falta", "Fehlt"),
    "subsystem.status_planned": ("Planned", "規劃中", "规划中", "Planificado", "Geplant"),
    "subsystem.coming_soon": ("Coming soon", "即將推出", "即将推出", "Próximamente", "Demnächst"),
}

_LOCALES = ("en", "zh_tw", "zh_cn", "es", "de")

CATALOG_STRINGS: dict[str, dict[str, str]] = {loc: {} for loc in _LOCALES}
for _name, _texts in _DESC.items():
    for _i, _loc in enumerate(_LOCALES):
        CATALOG_STRINGS[_loc][f"catalog.desc.{slug(_name)}"] = _texts[_i]
for _key, _texts in _SUBSYSTEM.items():
    for _i, _loc in enumerate(_LOCALES):
        CATALOG_STRINGS[_loc][_key] = _texts[_i]
