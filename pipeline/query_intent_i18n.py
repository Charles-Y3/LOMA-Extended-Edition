# -*- coding: utf-8 -*-
"""Shared multilingual concept-phrase matcher for query-intent classification.

Any code that decides routing/intent behavior (which deliverable to build, mutation
vs. generation, which role handles a request, whether to translate, whether to search
the web, which color to apply, etc.) by testing the user's own typed query/instruction
against a word list MUST use `matches()` (or a helper below) against a concept defined
here — never a hardcoded English-only tuple or regex. See CLAUDE.md section 8.

`matches()` checks a query against ALL supported locales, not just the active UI
locale — a user may type in a different language than their configured display
language, and intent detection should work regardless.
"""
from __future__ import annotations

import re

from pipeline.i18n import SUPPORTED_LOCALES

# ---------------------------------------------------------------------------
# Concept phrase tables. Each concept maps locale -> tuple of lowercase phrases
# (substring match, like the original per-file tuples they replace). Every
# concept must cover every locale in SUPPORTED_LOCALES.
# ---------------------------------------------------------------------------

CONCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
    "verb_edit_selection": {
        # Preview panel's "does this request change the highlighted text, or
        # just ask about it" check (services/session/preview_revision.py) —
        # an imperative edit verb routes to revise/mutate, anything else
        # defaults to ask/chat. "summarize"/"explain" are deliberately absent
        # (see that module's own comment) since for a selection those usually
        # mean "tell me", not "change this here".
        "en": (
            "revise", "edit", "change", "rewrite", "reword", "re-word", "rephrase",
            "fix", "correct", "replace", "translate", "localize", "localise",
            "update", "improve", "shorten", "lengthen", "expand", "condense",
            "simplify", "polish", "adjust", "convert", "reformat", "format",
            "proofread", "tidy", "capitalize", "capitalise", "bold", "make it",
            "make this", "turn it into", "turn this into", "clean up",
        ),
        "zh_tw": (
            "修改", "編輯", "改寫", "重寫", "換句話說", "修正", "更正", "取代", "替換",
            "翻譯", "在地化", "本地化", "更新", "改善", "縮短", "加長", "延長", "擴充",
            "精簡", "簡化", "潤飾", "潤色", "調整", "轉換", "重新格式化", "格式化",
            "校對", "整理", "大寫", "粗體", "改成", "變成", "清理",
        ),
        "zh_cn": (
            "修改", "编辑", "改写", "重写", "换句话说", "修正", "更正", "取代", "替换",
            "翻译", "本地化", "更新", "改善", "缩短", "加长", "延长", "扩充",
            "精简", "简化", "润饰", "润色", "调整", "转换", "重新格式化", "格式化",
            "校对", "整理", "大写", "粗体", "改成", "变成", "清理",
        ),
        "es": (
            "revisa", "edita", "cambia", "reescrib", "reformul", "parafrase",
            "corrig", "corrige", "reemplaza", "sustituye", "traduc", "localiza",
            "actualiza", "mejora", "acorta", "alarga", "expande", "amplía", "amplia",
            "condensa", "simplifica", "puli", "ajusta", "convierte", "reformatea",
            "formatea", "corrige la ortografía", "corrige la ortografia", "ordena",
            "mayúscula", "mayuscula", "negrita", "conviértelo", "conviertelo",
            "convierte esto", "limpia",
        ),
        "de": (
            "überarbeit", "ueberarbeit", "bearbeit", "änder", "aender", "umschreib",
            "umformulier", "paraphrasier", "korrigier", "ersetz", "übersetz",
            "uebersetz", "lokalisier", "aktualisier", "verbesser", "kürz", "kuerz",
            "verlänger", "verlaenger", "erweiter", "verdichte", "vereinfach",
            "poliere", "passe an", "konvertier", "formatiere neu", "formatiere",
            "korrekturlese", "räum auf", "raeum auf", "großschreib", "grossschreib",
            "fett", "mach daraus", "verwandel dies",
        ),
    },
    "image_deliverable_hints": {
        "en": (
            "generate an image", "create an image", "make an image", "draw me", "draw a",
            "draw an", "generate a picture", "create a picture", "make a picture",
            "picture of", "photo of", "illustration of", "image of", "logo", "poster",
            "cover art", "wallpaper", "icon for", "as a jpg", "as jpg", "as jpeg",
            "as a png", "as png",
        ),
        "zh_tw": (
            "生成一張圖", "產生一張圖片", "畫一個", "畫一張", "幫我畫", "...的圖片", "...的照片",
            "標誌", "海報", "桌布", "存成jpg", "存成png",
        ),
        "zh_cn": (
            "生成一张图", "生成一张图片", "画一个", "画一张", "帮我画", "...的图片", "...的照片",
            "标志", "海报", "壁纸", "存成jpg", "存成png",
        ),
        "es": (
            "generar una imagen", "crear una imagen", "hacer una imagen", "dibújame",
            "dibujame", "dibuja un", "dibuja una", "generar una foto", "foto de",
            "imagen de", "ilustración de", "ilustracion de", "logo", "logotipo",
            "cartel", "póster", "poster", "fondo de pantalla", "como jpg", "como png",
        ),
        "de": (
            "ein bild generieren", "ein bild erstellen", "ein bild machen", "zeichne mir",
            "zeichne ein", "foto von", "bild von", "illustration von", "logo", "poster",
            "hintergrundbild", "als jpg", "als png",
        ),
    },
    "wants_images": {
        "en": (
            "picture", "pictures", "photo", "photos", "illustration", "illustrations",
            "image", "images", "diagram", "chart", "drawing", "artwork", "visual",
            "include an image", "add an image", "with images",
        ),
        "zh_tw": (
            "圖片", "圖像", "照片", "插圖", "圖表", "繪圖", "視覺", "配圖", "加圖片", "附圖片",
        ),
        "zh_cn": (
            "图片", "图像", "照片", "插图", "图表", "绘图", "视觉", "配图", "加图片", "附图片",
        ),
        "es": (
            "imagen", "imágenes", "foto", "fotos", "ilustración", "ilustraciones",
            "diagrama", "gráfico", "dibujo", "visual", "con imágenes", "con imagenes",
            "incluir una imagen", "agregar una imagen", "añadir una imagen",
        ),
        "de": (
            "bild", "bilder", "foto", "fotos", "abbildung", "illustration", "diagramm",
            "grafik", "zeichnung", "visuell", "mit bildern", "ein bild einfügen",
            "ein bild hinzufügen",
        ),
    },
    "image_per_unit": {
        "en": (
            "each chapter", "every chapter", "per chapter", "each section", "every section",
            "per section", "each slide", "every slide", "per slide", "each part", "every part",
        ),
        "zh_tw": ("每章", "每一章", "每個章節", "每節", "每張投影片", "每一張投影片", "每部分"),
        "zh_cn": ("每章", "每一章", "每个章节", "每节", "每张幻灯片", "每一张幻灯片", "每部分"),
        "es": (
            "cada capítulo", "cada capitulo", "cada sección", "cada seccion",
            "cada diapositiva", "cada parte", "por capítulo", "por sección", "por diapositiva",
        ),
        "de": (
            "jedes kapitel", "jeden abschnitt", "jede folie", "jeden teil",
            "pro kapitel", "pro abschnitt", "pro folie",
        ),
    },
    "verb_translate": {
        "en": ("translate", "translation", "localize", "localise"),
        "zh_tw": ("翻譯", "譯成", "翻成"),
        "zh_cn": ("翻译", "译成", "翻成"),
        "es": ("traduc",),  # stem: traducir/traduce/traducción/traduciendo/...
        "de": ("übersetzen", "uebersetzen", "übersetzung", "uebersetzung"),
    },
    "verb_summarize": {
        "en": (
            "summarize", "summarise", "summary", "tldr", "tl;dr", "briefly explain",
            "key points", "main points", "executive summary", "overview", "digest", "recap",
            "highlights",
        ),
        "zh_tw": ("總結", "摘要", "概要", "重點", "概述", "簡述"),
        "zh_cn": ("总结", "摘要", "概要", "重点", "概述", "简述"),
        "es": (
            # "resum" stem catches resumir/resumen/resume/resumiendo (conjugations),
            # not just the infinitive — Spanish/German verbs conjugate, so a stem
            # substring is more robust than listing exact word forms one by one.
            "resum", "resúm", "puntos clave", "puntos principales",
            "en pocas palabras", "vista general",
        ),
        "de": (
            "zusammenfassen", "zusammenfassung", "kurz erklären", "kurz erklaeren",
            "wichtigste punkte", "überblick", "ueberblick", "kurzfassung",
        ),
    },
    "verb_analyze": {
        "en": (
            "analyse", "analyze", "analysis", "chart", "charts", "graph", "graphs", "plot",
            "visualiz", "dashboard", "statistics", "trend", "regression", "correlation",
            "distribution", "report", "produce",
        ),
        "zh_tw": ("分析", "圖表", "統計", "趨勢", "相關性", "分佈", "報告"),
        "zh_cn": ("分析", "图表", "统计", "趋势", "相关性", "分布", "报告"),
        "es": (
            "analiz", "anális", "gráfico", "grafico", "estadística", "estadistica",
            "tendencia", "correlación", "correlacion", "distribución", "informe",
            "produc",
        ),
        "de": (
            "analysier", "analyse", "diagramm", "statistik", "trend", "korrelation",
            "verteilung", "bericht", "erstell",
        ),
    },
    "verb_extract": {
        "en": ("extract", "entities", "action items", "key facts"),
        "zh_tw": ("提取", "萃取", "擷取", "重點事項", "關鍵事實"),
        "zh_cn": ("提取", "萃取", "摘取", "重点事项", "关键事实"),
        "es": ("extrae", "extrac", "puntos de acción", "hechos clave"),
        "de": ("extrahier", "extraktion", "wichtige fakten", "maßnahmen", "massnahmen"),
    },
    "verb_outline": {
        "en": ("outline", "plan", "structure", "steps"),
        "zh_tw": ("大綱", "綱要", "結構", "步驟"),
        "zh_cn": ("大纲", "纲要", "结构", "步骤"),
        "es": ("esquema", "esbozo", "estructura", "pasos"),
        "de": ("gliederung", "übersicht", "ueberuebersicht", "struktur", "schritte"),
    },
    "verb_rewrite": {
        "en": (
            "rewrite", "rephrase", "polish", "improve", "professional", "formal", "casual",
            "wording",
        ),
        "zh_tw": ("改寫", "重寫", "潤飾", "潤色", "改善", "正式", "口語"),
        "zh_cn": ("改写", "重写", "润饰", "润色", "改善", "正式", "口语"),
        "es": (
            "reescrib", "reformul", "puli", "mejora", "profesional", "formal",
            "informal", "redacción", "redaccion",
        ),
        "de": (
            "umschreib", "neu formulier", "verbesser", "professionell", "formell",
            "leger", "formulierung",
        ),
    },
    "verb_transcribe": {
        "en": (
            "transcribe", "transcript", "transcription", "what did they say", "what did he say",
            "what did she say", "subtitle", "captions", "diarize", "timestamp", "at what time",
        ),
        "zh_tw": ("轉錄", "逐字稿", "字幕", "說了什麼"),
        "zh_cn": ("转录", "逐字稿", "字幕", "说了什么"),
        "es": (
            "transcribir", "transcripción", "transcripcion", "subtítulos", "subtitulos",
            "qué dijeron", "que dijeron",
        ),
        "de": (
            "transkribieren", "transkript", "untertitel", "was haben sie gesagt",
        ),
    },
    "verb_rewrite_narrow": {
        # Just "rewrite/rephrase/paraphrase" — unlike "verb_rewrite" this excludes
        # "improve"/"polish"/tone words, which overlap with "discussion_signals" and
        # would wrongly reclassify a discussion-about-a-translation as a rewrite
        # request at call sites (extensions/viewer_runtime/highlight_runner.py) that
        # need the narrower sense.
        "en": ("rewrite", "rephrase", "paraphrase"),
        "zh_tw": ("改寫", "重寫", "換句話說"),
        "zh_cn": ("改写", "重写", "换句话说"),
        "es": ("reescrib", "reformul", "parafrase"),
        "de": ("umschreib", "umformulier", "paraphrasier"),
    },
    "verb_transcribe_raw": {
        "en": (
            "raw", "verbatim", "just show", "only show", "just the transcript",
            "only the transcript", "transcribe", "transcript", "transcription",
        ),
        "zh_tw": ("原始", "逐字", "轉錄", "逐字稿"),
        "zh_cn": ("原始", "逐字", "转录", "逐字稿"),
        "es": ("textual", "solo el transcripción", "solo la transcripción", "solo transcripcion"),
        "de": ("wörtlich", "woertlich", "nur das transkript", "nur die abschrift"),
    },
    "verb_write_author": {
        "en": ("write", "draft", "compose", "create a"),
        "zh_tw": ("撰寫", "寫一份", "草擬", "創作"),
        "zh_cn": ("撰写", "写一份", "草拟", "创作"),
        "es": ("escribir", "redactar", "componer", "crear un", "crear una"),
        "de": ("schreiben", "verfassen", "entwerfen", "erstellen"),
    },
    "image_mutation_hints": {
        "en": (
            "mutate", "mutation", "edit the image", "edit this image", "edit image",
            "change only", "change the", "change all", "change colour", "change color",
            "colour to", "color to", "color", "colour", "recolor", "re-color", "inpaint",
            "modify the image", "modify image", "adjust the", "replace the background",
            "keep the rest", "preserve",
        ),
        "zh_tw": (
            "修改圖片", "編輯圖片", "改成", "改變顏色", "改色", "重新上色", "修圖", "保留其餘",
            "背景替換",
        ),
        "zh_cn": (
            "修改图片", "编辑图片", "改成", "改变颜色", "改色", "重新上色", "修图", "保留其余",
            "背景替换",
        ),
        "es": (
            "editar la imagen", "editar esta imagen", "cambiar el color", "cambiar a color",
            "recolorear", "modificar la imagen", "reemplazar el fondo", "mantener el resto",
        ),
        "de": (
            "bild bearbeiten", "dieses bild bearbeiten", "farbe ändern", "farbe aendern",
            "umfärben", "umfaerben", "bild ändern", "bild aendern", "hintergrund ersetzen",
            "den rest beibehalten",
            # Bare "ändere"/"aendere" (change, imperative) — catches phrasing without
            # "bild"/"farbe" literally present, e.g. "ändere den frosch in schwarz"
            # (change the frog to black). Mirrors the English list's bare "color"/
            # "colour" entry for the same reason (see comment there).
            "ändere", "aendere", "änder", "aender",
        ),
    },
    "image_composite_hints": {
        "en": (
            "add", "place", "put", "insert", "copy", "paste", "combine", "merge", "fill",
            "empty", "slot", "another photo", "another image", "another picture",
        ),
        "zh_tw": ("加入", "放入", "插入", "貼上", "合併", "組合", "另一張照片", "另一張圖片"),
        "zh_cn": ("加入", "放入", "插入", "贴上", "合并", "组合", "另一张照片", "另一张图片"),
        "es": (
            "añadir", "agregar", "colocar", "insertar", "pegar", "combinar", "fusionar",
            "otra foto", "otra imagen",
        ),
        "de": (
            "hinzufügen", "hinzufuegen", "einfügen", "einsetzen", "einfügen", "kombinieren",
            "zusammenführen", "zusammenfuehren", "ein anderes foto", "ein anderes bild",
        ),
    },
    "capability_question": {
        "en": ("can you", "could you", "are you able to", "do you support"),
        "zh_tw": ("你可以", "你能不能", "你能夠", "你是否支援"),
        "zh_cn": ("你可以", "你能不能", "你能够", "你是否支持"),
        "es": ("puedes", "podrías", "podrias", "eres capaz de", "es compatible con"),
        "de": ("kannst du", "könntest du", "koenntest du", "bist du in der lage", "unterstützt du"),
    },
    "document_format_hint": {
        "en": ("word document", "document format", "docx"),
        "zh_tw": ("word文件", "文件格式", "docx檔"),
        "zh_cn": ("word文档", "文档格式", "docx文件"),
        "es": ("documento word", "formato de documento", "documento docx"),
        "de": ("word-dokument", "dokumentformat", "docx-datei"),
    },
    "presentation_format_hint": {
        "en": ("slide deck", "pptx", "presentation"),
        "zh_tw": ("投影片", "簡報", "pptx檔"),
        "zh_cn": ("幻灯片", "演示文稿", "pptx文件"),
        "es": ("diapositivas", "presentación", "presentacion", "archivo pptx"),
        "de": ("folien", "präsentation", "praesentation", "pptx-datei"),
    },
    "multi_step_separator": {
        "en": ("then", "and then", "after that", "next"),
        "zh_tw": ("然後", "接著", "之後"),
        "zh_cn": ("然后", "接着", "之后"),
        "es": ("luego", "después", "despues", "y después", "a continuación", "a continuacion"),
        "de": ("dann", "danach", "anschließend", "anschliessend"),
    },
    "per_source": {
        "en": ("each source", "every source", "per source", "each document", "each file", "each input"),
        "zh_tw": ("每個來源", "每份文件", "每個檔案"),
        "zh_cn": ("每个来源", "每份文档", "每个文件"),
        "es": ("cada fuente", "cada documento", "cada archivo"),
        "de": ("jede quelle", "jedes dokument", "jede datei"),
    },
    "combined_summary": {
        "en": (
            "combined", "single", "one summary", "overall", "together", "merge", "synthesize",
            "synthesise", "from all sources", "using both files",
        ),
        "zh_tw": ("合併", "整合", "統一摘要", "整體", "綜合", "所有來源"),
        "zh_cn": ("合并", "整合", "统一摘要", "整体", "综合", "所有来源"),
        "es": (
            "combinado", "combinada", "único resumen", "unico resumen", "en general",
            "juntos", "fusionar", "sintetizar", "de todas las fuentes",
        ),
        "de": (
            "kombiniert", "eine zusammenfassung", "insgesamt", "zusammen", "zusammenführen",
            "zusammenfuehren", "synthetisieren", "aus allen quellen",
        ),
    },
    "cross_source_compare": {
        "en": ("common", "compare", "comparison", "contrast", "difference", "differences", "across", "findings from"),
        "zh_tw": ("共同", "比較", "對比", "差異", "跨"),
        "zh_cn": ("共同", "比较", "对比", "差异", "跨"),
        "es": ("común", "comun", "comparar", "comparación", "comparacion", "contraste", "diferencia", "diferencias"),
        "de": ("gemeinsam", "vergleichen", "vergleich", "kontrast", "unterschied", "unterschiede"),
    },
    "selective_scope": {
        "en": ("only", "just"),
        "zh_tw": ("只", "僅", "只有"),
        "zh_cn": ("只", "仅", "只有"),
        "es": ("solo", "solamente", "únicamente", "unicamente"),
        "de": ("nur", "lediglich"),
    },
    "discussion_signals": {
        "en": (
            "good", "better", "accurate", "correct", "improve", "suggest", "alternative",
            "opinion", "quality", "review", "critique", "is the", "is this", "is that",
            "how is", "what do you think", "any other", "another way", "which is",
            "compare", "why", "explain",
        ),
        "zh_tw": ("好嗎", "更好", "準確", "正確", "改進", "建議", "另一種", "意見", "品質", "為什麼", "解釋"),
        "zh_cn": ("好吗", "更好", "准确", "正确", "改进", "建议", "另一种", "意见", "质量", "为什么", "解释"),
        "es": (
            "bueno", "mejor", "preciso", "correcto", "mejorar", "sugerir", "alternativa",
            "opinión", "opinion", "calidad", "revisar", "crítica", "critica", "por qué",
            "por que", "explicar",
        ),
        "de": (
            "gut", "besser", "genau", "korrekt", "verbessern", "vorschlagen", "alternative",
            "meinung", "qualität", "qualitaet", "überprüfen", "ueberpruefen", "kritik",
            "warum", "erklären", "erklaeren",
        ),
    },
    "live_grounding_patterns": {
        "en": (
            "weather", "forecast", "temperature", "today", "tonight", "latest", "recent",
            "current", "live score", "who won", "best restaurant", "best hotel", "cheapest",
            "price for", "how much", "where to", "opening hours", "exchange rate", "in stock",
        ),
        "zh_tw": (
            "天氣", "預報", "氣溫", "今天", "今晚", "最新", "最近", "目前", "即時比分", "誰贏了",
            "最好的餐廳", "最便宜", "價格", "多少錢", "營業時間", "匯率", "有庫存",
        ),
        "zh_cn": (
            "天气", "预报", "气温", "今天", "今晚", "最新", "最近", "目前", "实时比分", "谁赢了",
            "最好的餐厅", "最便宜", "价格", "多少钱", "营业时间", "汇率", "有库存",
        ),
        "es": (
            "clima", "pronóstico", "pronostico", "temperatura", "hoy", "esta noche",
            "último", "ultimo", "reciente", "actual", "resultado en vivo", "quién ganó",
            "quien gano", "mejor restaurante", "más barato", "mas barato", "precio de",
            "cuánto cuesta", "cuanto cuesta", "horario de apertura", "tipo de cambio",
            "en stock",
        ),
        "de": (
            "wetter", "vorhersage", "temperatur", "heute", "heute abend", "neueste",
            "aktuell", "live-ergebnis", "wer hat gewonnen", "bestes restaurant",
            "günstigste", "guenstigste", "preis für", "preis fuer", "wie viel kostet",
            "öffnungszeiten", "oeffnungszeiten", "wechselkurs", "auf lager",
        ),
    },
    "static_fact_patterns": {
        "en": ("highest mountain", "tallest mountain", "capital of", "who invented", "who discovered", "who wrote", "speed of light"),
        "zh_tw": ("最高的山", "首都", "誰發明了", "誰發現了", "誰寫的", "光速"),
        "zh_cn": ("最高的山", "首都", "谁发明了", "谁发现了", "谁写的", "光速"),
        "es": ("montaña más alta", "capital de", "quién inventó", "quien invento", "quién descubrió", "quién escribió", "velocidad de la luz"),
        "de": ("höchster berg", "hoechster berg", "hauptstadt von", "wer erfand", "wer entdeckte", "wer schrieb", "lichtgeschwindigkeit"),
    },
    "greeting_only": {
        "en": ("hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "bye"),
        "zh_tw": ("嗨", "你好", "謝謝", "掰掰", "再見"),
        "zh_cn": ("嗨", "你好", "谢谢", "拜拜", "再见"),
        "es": ("hola", "gracias", "vale", "ok", "adiós", "adios"),
        "de": ("hallo", "hi", "danke", "ok", "okay", "tschüss", "tschuess"),
    },
    "creative_writing_skip": {
        "en": ("once upon a time", "write a story", "write a poem", "tell me a joke", "chapter"),
        "zh_tw": ("從前", "寫一個故事", "寫一首詩", "說個笑話"),
        "zh_cn": ("从前", "写一个故事", "写一首诗", "说个笑话"),
        "es": ("érase una vez", "erase una vez", "escribe una historia", "escribe un poema", "cuéntame un chiste", "cuentame un chiste"),
        "de": ("es war einmal", "schreib eine geschichte", "schreib ein gedicht", "erzähl mir einen witz", "erzaehl mir einen witz"),
    },
    "question_start_words": {
        "en": ("what", "which", "where", "when", "who", "how", "is", "are", "can", "could", "does", "do", "did", "will", "should"),
        "zh_tw": ("什麼", "哪個", "哪裡", "何時", "誰", "如何", "是否", "能不能", "會不會"),
        "zh_cn": ("什么", "哪个", "哪里", "何时", "谁", "如何", "是否", "能不能", "会不会"),
        "es": ("qué", "que", "cuál", "cual", "dónde", "donde", "cuándo", "cuando", "quién", "quien", "cómo", "como", "es", "son", "puede", "podría"),
        "de": ("was", "welche", "wo", "wann", "wer", "wie", "ist", "sind", "kann", "könnte", "koennte"),
    },
    "date_intent": {
        "en": ("date", "when", "what year", "what date", "time period", "date range"),
        "zh_tw": ("日期", "幾時", "什麼時候", "年份", "期間", "哪一年"),
        "zh_cn": ("日期", "几时", "什么时候", "年份", "期间", "哪一年"),
        "es": ("fecha", "cuándo", "cuando", "qué año", "que año", "período", "periodo", "rango de fechas"),
        "de": ("datum", "wann", "welches jahr", "zeitraum", "datumsbereich"),
    },
    "analysis_style_query": {
        "en": (
            "summarize", "summarise", "compare", "comparison", "list all", "report",
            "across", "synthesize", "synthesise", "themes", "overview", "trends",
            "differences", "similarities",
        ),
        "zh_tw": ("總結", "摘要", "比較", "綜合", "主題", "報告", "差異", "相似"),
        "zh_cn": ("总结", "摘要", "比较", "综合", "主题", "报告", "差异", "相似"),
        "es": (
            "resumir", "comparar", "comparación", "comparacion", "listar todo", "informe",
            "sintetizar", "temas", "resumen general", "tendencias", "diferencias", "similitudes",
        ),
        "de": (
            "zusammenfassen", "vergleichen", "vergleich", "alle auflisten", "bericht",
            "synthetisieren", "themen", "überblick", "ueberblick", "trends", "unterschiede",
            "gemeinsamkeiten",
        ),
    },
    "include_images_yes": {
        "en": ("include image", "include images", "with pictures", "with photos", "use visuals", "add images", "illustrated"),
        "zh_tw": ("包含圖片", "附上照片", "使用視覺", "加圖片", "有插圖"),
        "zh_cn": ("包含图片", "附上照片", "使用视觉", "加图片", "有插图"),
        "es": ("incluir imagen", "incluir imágenes", "incluir imagenes", "con fotos", "usar visuales", "ilustrado"),
        "de": ("bild einschließen", "bilder einschliessen", "mit fotos", "visuals verwenden", "illustriert"),
    },
    "include_images_no": {
        "en": ("no images", "without images", "text only", "no pictures", "no photos"),
        "zh_tw": ("不要圖片", "沒有圖片", "純文字", "不要照片"),
        "zh_cn": ("不要图片", "没有图片", "纯文字", "不要照片"),
        "es": ("sin imágenes", "sin imagenes", "solo texto", "sin fotos"),
        "de": ("keine bilder", "ohne bilder", "nur text", "keine fotos"),
    },
    "tone_formal": {
        "en": ("formal", "executive", "board", "stakeholder", "professional"),
        "zh_tw": ("正式", "高層", "董事會", "利害關係人", "專業"),
        "zh_cn": ("正式", "高层", "董事会", "利益相关者", "专业"),
        "es": ("formal", "ejecutivo", "junta directiva", "parte interesada", "profesional"),
        "de": ("formell", "geschäftsführung", "geschaeftsfuehrung", "vorstand", "stakeholder", "professionell"),
    },
    "tone_casual": {
        "en": ("casual", "friendly", "informal", "lighthearted"),
        "zh_tw": ("輕鬆", "友善", "非正式"),
        "zh_cn": ("轻松", "友善", "非正式"),
        "es": ("casual", "amigable", "informal", "desenfadado"),
        "de": ("locker", "freundlich", "informell", "unbeschwert"),
    },
    "tone_academic": {
        "en": ("academic", "scholarly", "thesis", "research", "lecture"),
        "zh_tw": ("學術", "論文", "研究", "講座"),
        "zh_cn": ("学术", "论文", "研究", "讲座"),
        "es": ("académico", "academico", "erudito", "tesis", "investigación", "investigacion", "conferencia"),
        "de": ("akademisch", "wissenschaftlich", "these", "forschung", "vorlesung"),
    },
    "tone_persuasive": {
        "en": ("persuasive", "pitch", "sell", "convince", "marketing"),
        "zh_tw": ("說服", "推銷", "行銷"),
        "zh_cn": ("说服", "推销", "营销"),
        "es": ("persuasivo", "propuesta", "vender", "convencer", "marketing"),
        "de": ("überzeugend", "ueberzeugend", "pitch", "verkaufen", "überzeugen", "ueberzeugen", "marketing"),
    },
    "tone_technical": {
        "en": ("technical", "engineering", "api", "architecture", "spec"),
        "zh_tw": ("技術", "工程", "架構", "規格"),
        "zh_cn": ("技术", "工程", "架构", "规格"),
        "es": ("técnico", "tecnico", "ingeniería", "ingenieria", "arquitectura", "especificación", "especificacion"),
        "de": ("technisch", "technik", "architektur", "spezifikation"),
    },
    "speaker_notes_request": {
        "en": ("speaker notes", "presenter notes", "notes for each slide", "notes for every slide"),
        "zh_tw": ("演講者備註", "簡報者筆記", "每張投影片的備註"),
        "zh_cn": ("演讲者备注", "演示者笔记", "每张幻灯片的备注"),
        "es": ("notas del orador", "notas del presentador", "notas para cada diapositiva"),
        "de": ("sprechernotizen", "referentennotizen", "notizen für jede folie", "notizen fuer jede folie"),
    },
    "cover_off": {
        "en": ("no cover", "without cover", "skip cover", "remove cover", "don't want a cover", "no cover page"),
        "zh_tw": ("不要封面", "沒有封面", "跳過封面", "移除封面"),
        "zh_cn": ("不要封面", "没有封面", "跳过封面", "移除封面"),
        "es": ("sin portada", "sin página de portada", "omitir portada", "quitar portada"),
        "de": ("kein deckblatt", "ohne deckblatt", "deckblatt überspringen", "deckblatt weglassen"),
    },
    "toc_off": {
        "en": ("no toc", "without toc", "no table of contents", "skip table of contents", "remove table of contents", "no contents page"),
        "zh_tw": ("不要目錄", "沒有目錄", "跳過目錄"),
        "zh_cn": ("不要目录", "没有目录", "跳过目录"),
        "es": ("sin índice", "sin indice", "sin tabla de contenido", "omitir tabla de contenido"),
        "de": ("kein inhaltsverzeichnis", "ohne inhaltsverzeichnis", "inhaltsverzeichnis überspringen", "inhaltsverzeichnis weglassen"),
    },
    "preserve_edit_hints": {
        "en": (
            "only", "just", "preserve", "keep", "unchanged", "don't change", "do not change",
            "without changing", "same", "identical", "exact", "pixel", "background only",
            "remove only", "replace only", "edit only", "mask", "colour", "color", "recolor",
        ),
        "zh_tw": ("只", "僅", "保留", "保持", "不變", "不要改變", "相同", "一樣", "背景", "遮罩", "顏色"),
        "zh_cn": ("只", "仅", "保留", "保持", "不变", "不要改变", "相同", "一样", "背景", "遮罩", "颜色"),
        "es": (
            "solo", "solamente", "preservar", "mantener", "sin cambios", "no cambiar",
            "sin cambiar", "igual", "idéntico", "identico", "exacto", "píxel", "pixel",
            "solo el fondo", "solo eliminar", "solo reemplazar", "solo editar", "máscara",
            "mascara", "color", "recolorear",
        ),
        "de": (
            "nur", "lediglich", "beibehalten", "bewahren", "unverändert", "unveraendert",
            "nicht ändern", "nicht aendern", "gleich", "identisch", "exakt", "pixel",
            "nur der hintergrund", "nur entfernen", "nur ersetzen", "nur bearbeiten",
            "maske", "farbe", "umfärben", "umfaerben",
        ),
    },
    "media_holistic": {
        "en": (
            "what is it about", "what's it about", "what is this about", "what is the video about",
            "what is in the video", "what happens in", "what is happening", "what is going on",
            "what are they doing", "explain the video", "summarize the video", "summarise the video",
            "tell me about this video", "content of the video", "overall message", "main idea",
            "gist of",
        ),
        "zh_tw": ("這是關於什麼", "影片在講什麼", "發生了什麼事", "解釋這部影片", "總結這部影片", "主旨"),
        "zh_cn": ("这是关于什么", "视频在讲什么", "发生了什么事", "解释这个视频", "总结这个视频", "主旨"),
        "es": (
            "de qué trata", "de que trata", "de qué se trata el video", "qué pasa en",
            "qué está pasando", "explica el video", "resume el video", "idea principal",
            "de qué se trata",
        ),
        "de": (
            "worum geht es", "worum geht es in dem video", "was passiert in", "was ist los",
            "erkläre das video", "erklaere das video", "fasse das video zusammen",
            "hauptidee", "worum geht's",
        ),
    },
    "media_vision_only": {
        "en": (
            "what do you see", "describe the scene", "describe what you see", "visually",
            "what color", "what colour", "what does it look", "read the text on",
            "text on screen", "on-screen text", "who appears", "who is in the frame",
            "logo", "banner", "screenshot", "clothing", "what are they wearing",
        ),
        "zh_tw": ("你看到什麼", "描述場景", "畫面上的文字", "誰出現", "穿什麼衣服", "螢幕截圖"),
        "zh_cn": ("你看到什么", "描述场景", "画面上的文字", "谁出现", "穿什么衣服", "屏幕截图"),
        "es": (
            "qué ves", "que ves", "describe la escena", "visualmente", "qué color",
            "que color", "texto en pantalla", "quién aparece", "quien aparece",
            "captura de pantalla", "qué llevan puesto",
        ),
        "de": (
            "was siehst du", "beschreibe die szene", "visuell", "welche farbe",
            "text auf dem bildschirm", "wer erscheint", "screenshot", "was tragen sie",
        ),
    },
    "media_transcript_only": {
        "en": (
            "transcribe", "transcript", "transcription", "what did they say", "what did he say",
            "what did she say", "timestamp", "at what time", "minute mark", "subtitle",
            "captions", "caption", "diarize", "quote from", "when does", "when do they",
        ),
        "zh_tw": ("轉錄", "逐字稿", "說了什麼", "時間戳", "第幾分鐘", "字幕", "引用"),
        "zh_cn": ("转录", "逐字稿", "说了什么", "时间戳", "第几分钟", "字幕", "引用"),
        "es": (
            "transcribe", "transcripción", "transcripcion", "qué dijeron", "que dijeron",
            "marca de tiempo", "subtítulos", "subtitulos", "cita de",
        ),
        "de": (
            "transkribiere", "transkript", "was haben sie gesagt", "zeitstempel",
            "untertitel", "zitat von",
        ),
    },
    "media_full_transcript": {
        "en": (
            "full transcript", "entire transcript", "complete transcript", "verbatim",
            "word for word", "whole transcript", "transcript only",
        ),
        "zh_tw": ("完整逐字稿", "全部逐字稿", "逐字", "只要逐字稿"),
        "zh_cn": ("完整逐字稿", "全部逐字稿", "逐字", "只要逐字稿"),
        "es": (
            "transcripción completa", "transcripcion completa", "textual", "palabra por palabra",
            "solo la transcripción", "solo la transcripcion",
        ),
        "de": (
            "vollständiges transkript", "vollstaendiges transkript", "wörtlich", "woertlich",
            "wort für wort", "wort fuer wort", "nur das transkript",
        ),
    },
    "weather_words": {
        "en": ("weather", "forecast", "temperature", "rain", "raining", "snow", "humidity", "feels like"),
        "zh_tw": ("天氣", "預報", "氣溫", "下雨", "下雪", "濕度", "體感溫度"),
        "zh_cn": ("天气", "预报", "气温", "下雨", "下雪", "湿度", "体感温度"),
        "es": ("clima", "tiempo", "pronóstico", "pronostico", "temperatura", "lluvia", "nieve", "humedad", "sensación térmica", "sensacion termica"),
        "de": ("wetter", "vorhersage", "temperatur", "regen", "schnee", "luftfeuchtigkeit", "gefühlte temperatur", "gefuehlte temperatur"),
    },
    "weather_extreme_words": {
        "en": ("highest", "lowest", "hottest", "coldest", "warmest", "coolest", "maximum", "minimum", "extreme", "record"),
        "zh_tw": ("最高", "最低", "最熱", "最冷", "極端", "紀錄"),
        "zh_cn": ("最高", "最低", "最热", "最冷", "极端", "纪录"),
        "es": ("más alto", "mas alto", "más bajo", "mas bajo", "más caliente", "mas caliente", "más frío", "mas frio", "máximo", "maximo", "mínimo", "minimo", "extremo", "récord", "record"),
        "de": ("höchste", "hoechste", "niedrigste", "heißeste", "heisseste", "kälteste", "kaelteste", "maximal", "minimal", "extrem", "rekord"),
    },
    "weather_global_location_words": {
        "en": ("the world", "worldwide", "global", "the globe", "the planet", "everywhere"),
        "zh_tw": ("全世界", "全球", "世界各地"),
        "zh_cn": ("全世界", "全球", "世界各地"),
        "es": ("el mundo", "mundial", "global", "todo el mundo", "en todas partes"),
        "de": ("die welt", "weltweit", "global", "überall", "ueberall"),
    },
    "topic_warm_coral": {
        "en": ("kindness", "compassion", "empathy", "care", "community", "love", "gratitude"),
        "zh_tw": ("善良", "同理心", "關懷", "社群", "愛", "感恩"),
        "zh_cn": ("善良", "同理心", "关怀", "社群", "爱", "感恩"),
        "es": ("bondad", "compasión", "compasion", "empatía", "empatia", "cuidado", "comunidad", "amor", "gratitud"),
        "de": ("freundlichkeit", "mitgefühl", "mitgefuehl", "empathie", "fürsorge", "fuersorge", "gemeinschaft", "liebe", "dankbarkeit"),
    },
    "topic_ocean_teal": {
        "en": ("health", "wellness", "medical", "hospital"),
        "zh_tw": ("健康", "醫療", "醫院"),
        "zh_cn": ("健康", "医疗", "医院"),
        "es": ("salud", "bienestar", "médico", "medico", "hospital"),
        "de": ("gesundheit", "wohlbefinden", "medizinisch", "krankenhaus"),
    },
    "topic_forest_green": {
        "en": ("environment", "climate", "nature", "sustain", "green"),
        "zh_tw": ("環境", "氣候", "自然", "永續", "綠色"),
        "zh_cn": ("环境", "气候", "自然", "可持续", "绿色"),
        "es": ("medio ambiente", "clima", "naturaleza", "sostenible", "verde"),
        "de": ("umwelt", "klima", "natur", "nachhaltig", "grün", "gruen"),
    },
    "topic_royal_purple": {
        "en": ("creative", "design", "brand", "marketing", "story"),
        "zh_tw": ("創意", "設計", "品牌", "行銷", "故事"),
        "zh_cn": ("创意", "设计", "品牌", "营销", "故事"),
        "es": ("creativo", "diseño", "diseno", "marca", "marketing", "historia"),
        "de": ("kreativ", "design", "marke", "marketing", "geschichte"),
    },
    "topic_slate_modern": {
        "en": ("finance", "business", "corporate", "strategy", "quarter"),
        "zh_tw": ("財務", "商業", "企業", "策略", "季度"),
        "zh_cn": ("财务", "商业", "企业", "战略", "季度"),
        "es": ("finanzas", "negocio", "corporativo", "estrategia", "trimestre"),
        "de": ("finanzen", "geschäft", "geschaeft", "unternehmen", "strategie", "quartal"),
    },
    "topic_midnight_blue": {
        "en": ("education", "learning", "school", "training"),
        "zh_tw": ("教育", "學習", "學校", "培訓"),
        "zh_cn": ("教育", "学习", "学校", "培训"),
        "es": ("educación", "educacion", "aprendizaje", "escuela", "capacitación", "capacitacion"),
        "de": ("bildung", "lernen", "schule", "schulung"),
    },
    "topic_sunset_amber": {
        "en": ("energy", "startup", "pitch", "innovation"),
        "zh_tw": ("能源", "新創", "提案", "創新"),
        "zh_cn": ("能源", "创业", "提案", "创新"),
        "es": ("energía", "energia", "startup", "propuesta", "innovación", "innovacion"),
        "de": ("energie", "startup", "pitch", "innovation"),
    },
    "spreadsheet_money": {
        "en": ("salary", "salaries", "wage", "wages", "pay", "paid", "earn", "earns", "earning",
               "money", "income", "revenue", "sales", "price", "cost", "amount", "fee", "compensation"),
        "zh_tw": ("薪水", "薪資", "工資", "收入", "營收", "銷售額", "價格", "成本", "金額", "費用"),
        "zh_cn": ("薪水", "薪资", "工资", "收入", "营收", "销售额", "价格", "成本", "金额", "费用"),
        "es": ("salario", "salarios", "sueldo", "paga", "ingreso", "ingresos", "ganar", "dinero",
               "precio", "costo", "coste", "monto", "tarifa", "compensación", "compensacion"),
        "de": ("gehalt", "lohn", "bezahlung", "einkommen", "umsatz", "verkauf", "preis", "kosten",
               "betrag", "gebühr", "gebuehr", "vergütung", "verguetung"),
    },
    "spreadsheet_rating": {
        "en": ("rating", "ratings", "score", "scores", "performance", "perform", "grade", "rank", "ranking"),
        "zh_tw": ("評分", "分數", "績效", "表現", "等級", "排名"),
        "zh_cn": ("评分", "分数", "绩效", "表现", "等级", "排名"),
        "es": ("calificación", "calificacion", "puntuación", "puntuacion", "rendimiento", "desempeño",
               "desempeno", "grado", "clasificación", "clasificacion"),
        "de": ("bewertung", "punktzahl", "leistung", "note", "rang", "ranking"),
    },
    "spreadsheet_category": {
        "en": ("department", "dept", "region", "team", "category", "group", "division", "branch",
               "status", "type", "class", "segment", "channel", "platform", "country", "city", "state"),
        "zh_tw": ("部門", "地區", "團隊", "類別", "組", "分部", "狀態", "類型", "國家", "城市"),
        "zh_cn": ("部门", "地区", "团队", "类别", "组", "分部", "状态", "类型", "国家", "城市"),
        "es": ("departamento", "región", "region", "equipo", "categoría", "categoria", "grupo",
               "división", "division", "estado", "tipo", "país", "pais", "ciudad"),
        "de": ("abteilung", "region", "team", "kategorie", "gruppe", "bereich", "status", "typ",
               "land", "stadt"),
    },
    "spreadsheet_entity_count": {
        "en": ("employee", "employees", "staff", "people", "worker", "workers", "customer", "customers",
               "user", "users", "member", "members", "record", "records", "row", "rows", "item", "items"),
        "zh_tw": ("員工", "人員", "客戶", "使用者", "用戶", "會員", "記錄", "筆", "項目"),
        "zh_cn": ("员工", "人员", "客户", "使用者", "用户", "会员", "记录", "条", "项目"),
        "es": ("empleado", "empleados", "personal", "trabajador", "cliente", "clientes", "usuario",
               "usuarios", "miembro", "miembros", "registro", "registros", "fila", "filas"),
        "de": ("mitarbeiter", "personal", "kunde", "kunden", "benutzer", "mitglied", "mitglieder",
               "datensatz", "zeile", "zeilen"),
    },
    "which_what_words": {
        "en": ("which", "what"),
        "zh_tw": ("哪個", "哪一個", "什麼", "哪些"),
        "zh_cn": ("哪个", "哪一个", "什么", "哪些"),
        "es": ("cuál", "cual", "cuáles", "cuales", "qué", "que"),
        "de": ("welche", "welcher", "welches", "was"),
    },
    "superlative_max_words": {
        "en": ("best", "top", "highest", "most", "max", "maximum", "largest", "many"),
        "zh_tw": ("最好", "最高", "最多", "最大"),
        "zh_cn": ("最好", "最高", "最多", "最大"),
        "es": ("mejor", "más alto", "mas alto", "más", "mas", "máximo", "maximo", "mayor"),
        "de": ("beste", "höchste", "hoechste", "meiste", "maximal", "größte", "groesste"),
    },
    "superlative_min_words": {
        "en": ("lowest", "worst", "least", "fewest", "minimum", "min"),
        "zh_tw": ("最低", "最差", "最少"),
        "zh_cn": ("最低", "最差", "最少"),
        "es": ("más bajo", "mas bajo", "peor", "menos", "mínimo", "minimo", "menor"),
        "de": ("niedrigste", "schlechteste", "wenigste", "minimal", "kleinste"),
    },
    "diagram_image_hints": {
        "en": (
            "flowchart", "flow chart", "workflow diagram", "process diagram",
            "pipeline diagram", "sequence diagram", "diagram of the process",
            "diagram of the steps", "steps for", "workflow for", "process for",
            "diagram showing", "workflow image", "pipeline image",
            "org chart", "organization chart", "organizational chart",
            "sales funnel", "funnel diagram", "diagram about", "diagram for",
            "diagram of",
        ),
        "zh_tw": (
            "流程圖", "工作流程圖", "流程", "步驟圖", "管線圖", "流程示意圖",
            "組織圖", "組織架構圖", "銷售漏斗",
        ),
        "zh_cn": (
            "流程图", "工作流程图", "流程", "步骤图", "管线图", "流程示意图",
            "组织图", "组织架构图", "销售漏斗",
        ),
        "es": (
            "diagrama de flujo", "diagrama de proceso", "diagrama de pasos",
            "diagrama del proceso", "imagen del flujo de trabajo", "pasos para",
            "flujo de trabajo para", "organigrama", "embudo de ventas",
        ),
        "de": (
            "flussdiagramm", "ablaufdiagramm", "prozessdiagramm", "workflow-diagramm",
            "diagramm der schritte", "schritte für", "schritte fuer",
            "arbeitsablauf für", "arbeitsablauf fuer", "organigramm",
            "verkaufstrichter",
        ),
    },
    "chart_image_hints": {
        "en": (
            "line chart", "line graph", "bar chart", "bar graph", "pie chart",
            "chart of", "graph of", "plot of", "chart showing", "graph showing",
            "plot showing", "trend chart", "chart illustrating", "graph illustrating",
            "chart depicting", "graph depicting", "chart comparing", "graph comparing",
            "chart visualizing", "graph visualizing", "data visualization",
            "chart about", "graph about", "plot about", "chart for", "graph for",
        ),
        "zh_tw": ("折線圖", "長條圖", "柱狀圖", "圓餅圖", "趨勢圖", "統計圖"),
        "zh_cn": ("折线图", "条形图", "柱状图", "饼图", "趋势图", "统计图"),
        "es": (
            "gráfico de líneas", "grafico de lineas", "gráfico de barras",
            "grafico de barras", "gráfico circular", "grafico circular",
            "gráfico de", "grafico de", "gráfica de", "grafica de",
            "gráfico sobre", "grafico sobre",
        ),
        "de": (
            "liniendiagramm", "balkendiagramm", "kreisdiagramm", "diagramm von",
            "diagramm für", "diagramm fuer", "grafik von", "diagramm über",
            "diagramm ueber",
        ),
    },
    "infographic_comparison_hints": {
        "en": (
            "compare", "comparison of", "comparison chart", "vs", "versus",
            "side by side comparison", "which is better",
        ),
        "zh_tw": ("比較", "對比", "比較表", "對比表", "哪個更好"),
        "zh_cn": ("比较", "对比", "比较表", "对比表", "哪个更好"),
        "es": (
            "comparar", "comparación de", "comparacion de", "tabla comparativa",
            "versus", "cuál es mejor", "cual es mejor",
        ),
        "de": (
            "vergleiche", "vergleich von", "vergleichstabelle", "versus",
            "was ist besser",
        ),
    },
    "poster_style_lower_third_hints": {
        "en": ("lower third", "caption band", "scenic with caption", "photo with caption"),
        "zh_tw": ("下三分之一", "底部標題", "底部字幕"),
        "zh_cn": ("下三分之一", "底部标题", "底部字幕"),
        "es": ("tercio inferior", "franja de título", "foto con título abajo"),
        "de": ("unteres drittel", "titelband unten", "foto mit titel unten"),
    },
    "poster_style_top_banner_hints": {
        "en": ("top banner", "header banner", "banner style", "announcement style", "announcement banner"),
        "zh_tw": ("頂部橫幅", "標題橫幅", "公告風格"),
        "zh_cn": ("顶部横幅", "标题横幅", "公告风格"),
        "es": ("banner superior", "estilo banner", "estilo anuncio"),
        "de": ("oberes banner", "banner-stil", "ankündigungsstil"),
    },
    "poster_style_centered_badge_hints": {
        "en": ("centered badge", "badge style", "emblem style", "seal style", "logo style"),
        "zh_tw": ("置中徽章", "徽章風格", "印章風格"),
        "zh_cn": ("居中徽章", "徽章风格", "印章风格"),
        "es": ("insignia centrada", "estilo insignia", "estilo sello"),
        "de": ("zentriertes abzeichen", "abzeichen-stil", "siegel-stil"),
    },
    "presentation_style_minimal_hints": {
        "en": ("minimal presentation", "minimal deck", "minimal style presentation", "minimalist presentation"),
        "zh_tw": ("簡約風格簡報", "極簡簡報", "簡約簡報"),
        "zh_cn": ("简约风格演示文稿", "极简演示文稿", "简约演示文稿"),
        "es": ("presentación minimalista", "presentación estilo minimalista"),
        "de": ("minimalistische präsentation", "präsentation im minimal-stil"),
    },
    "presentation_style_bold_hints": {
        "en": ("bold presentation", "editorial style presentation", "bold editorial deck", "bold style presentation", "magazine style presentation"),
        "zh_tw": ("大膽風格簡報", "雜誌風格簡報", "編輯風格簡報"),
        "zh_cn": ("大胆风格演示文稿", "杂志风格演示文稿", "编辑风格演示文稿"),
        "es": ("presentación audaz", "presentación estilo editorial", "presentación estilo revista"),
        "de": ("mutige präsentation", "präsentation im editorial-stil", "präsentation im magazin-stil"),
    },
    "presentation_style_insight_hints": {
        "en": ("insight presentation", "insight deck", "insight-driven presentation", "chart-forward presentation", "data-heavy presentation", "chart-heavy presentation"),
        "zh_tw": ("洞察風格簡報", "洞察簡報", "圖表導向簡報", "數據導向簡報"),
        "zh_cn": ("洞察风格演示文稿", "洞察演示文稿", "图表导向演示文稿", "数据导向演示文稿"),
        "es": ("presentación de perspectivas", "presentación orientada a gráficos", "presentación con muchos datos"),
        "de": ("einblick-präsentation", "diagrammorientierte präsentation", "datenlastige präsentation"),
    },
    # Stage-2 safety net for pipeline/direct/image_intent.py::classify_image_request()
    # — bare/loose words, not fixed phrases, deliberately broader than the
    # chart_image_hints/diagram_image_hints/infographic_*_hints phrase lists above.
    # Only used INSIDE classify_image_request(), which only ever runs once a request
    # is already known to want an image (output_type == "image") — so a bare "chart"
    # mention can't misroute an unrelated chat question into image generation the way
    # it could if used at the earlier output-type-decision stage. Exists so a single
    # unanticipated preposition/verb ("chart about X", "diagram regarding X") doesn't
    # permanently fall through to a generic hallucinated photo the way it used to —
    # every new phrasing that reaches this point at least has a chance to be caught,
    # instead of only the phrases someone thought to enumerate ahead of time.
    # "graph" deliberately excluded — as a bare substring it collides with
    # "photograph"/"photography"/"infographic"/"paragraph", silently misrouting
    # completely unrelated requests into chart generation (found and confirmed
    # via the regression matrix before this ever shipped). "graph of"/"graph
    # about"/"graph showing" etc. are still covered by chart_image_hints above
    # as full phrases, which don't have this collision problem.
    "chart_broad_keywords": {
        "en": ("chart", "statistic", "percentage", "data point"),
        "zh_tw": ("圖表", "統計", "百分比"),
        "zh_cn": ("图表", "统计", "百分比"),
        "es": ("gráfico", "grafico", "gráfica", "grafica", "estadística", "estadistica", "porcentaje"),
        "de": ("diagramm", "statistik", "prozentsatz"),
    },
    "diagram_broad_keywords": {
        "en": ("diagram", "flowchart", "flow chart", "workflow", "process map"),
        "zh_tw": ("圖解", "流程", "工作流程"),
        "zh_cn": ("图解", "流程", "工作流程"),
        "es": ("diagrama", "flujo de trabajo"),
        "de": ("diagramm", "arbeitsablauf"),
    },
    "infographic_broad_keywords": {
        "en": ("infographic", "checklist", "key facts", "at a glance"),
        "zh_tw": ("資訊圖", "檢查清單", "重點"),
        "zh_cn": ("信息图", "检查清单", "要点"),
        "es": ("infografía", "infografia", "lista de verificación", "lista de verificacion"),
        "de": ("infografik", "checkliste"),
    },
    "background_change_target_hints": {
        "en": (
            "change the background", "replace the background", "new background",
            "background to", "swap the background", "background image", "backdrop to",
        ),
        "zh_tw": ("更改背景", "換背景", "換成", "背景改成", "背景換成", "替換背景"),
        "zh_cn": ("更改背景", "换背景", "换成", "背景改成", "背景换成", "替换背景"),
        "es": (
            "cambiar el fondo", "cambia el fondo", "reemplazar el fondo",
            "nuevo fondo", "fondo a", "el fondo por",
        ),
        "de": (
            "hintergrund ändern", "hintergrund aendern", "hintergrund ersetzen",
            "neuer hintergrund", "hintergrund zu", "hintergrund durch",
        ),
    },
    "wants_variations": {
        "en": (
            "a few options", "some options", "some variations", "a few variations",
            "give me options", "give me variations", "a couple of versions",
            "multiple options", "different options", "several options",
        ),
        "zh_tw": ("幾個選項", "一些選項", "幾種變化", "多個版本", "幾個版本"),
        "zh_cn": ("几个选项", "一些选项", "几种变化", "多个版本", "几个版本"),
        "es": (
            "algunas opciones", "unas opciones", "varias opciones",
            "algunas variaciones", "unas variaciones", "diferentes opciones",
            "varias versiones",
        ),
        "de": (
            "ein paar optionen", "einige optionen", "mehrere optionen",
            "ein paar varianten", "einige varianten", "verschiedene optionen",
            "mehrere versionen",
        ),
    },
    "poster_image_hints": {
        # "infographic" deliberately NOT here — it used to be, back when a poster
        # and an infographic were the same single-image capability, but they're
        # now distinct renderers (see infographic_stat_hints/infographic_timeline_
        # hints/infographic_comparison_hints); leaving it here made ANY mention of
        # "infographic" (even a bare "give me an infographic about X", or a
        # presentation request that merely mentions embedding infographics)
        # collapse to "poster" instead of the real infographic renderer or
        # (for presentations) the correct output type entirely.
        "en": (
            "poster", "banner", "flyer", "cover art", "poster about",
            "with the text", "that says", "saying",
        ),
        "zh_tw": ("海報", "橫幅", "傳單", "封面", "寫著", "文字寫"),
        "zh_cn": ("海报", "横幅", "传单", "封面", "写着", "文字写"),
        "es": (
            "póster", "poster", "cartel", "folleto", "portada",
            "que diga", "con el texto",
        ),
        "de": (
            "poster", "banner", "flyer", "titelbild", "mit dem text",
            "auf dem steht",
        ),
    },
    "infographic_stat_hints": {
        "en": (
            "key facts", "quick facts", "did you know", "by the numbers",
            "fast facts", "stat grid", "at a glance", "things you should know",
            "quick stats", "infographic", "infographics",
        ),
        "zh_tw": ("重點事實", "小知識", "你知道嗎", "數據一覽", "快速統計", "一覽表", "資訊圖"),
        "zh_cn": ("重点事实", "小知识", "你知道吗", "数据一览", "快速统计", "一览表", "信息图"),
        "es": (
            "datos clave", "datos rápidos", "datos rapidos", "sabías que",
            "sabias que", "en cifras", "de un vistazo", "datos curiosos",
            "infografía", "infografia",
        ),
        "de": (
            "wichtige fakten", "schnelle fakten", "wusstest du", "auf einen blick",
            "kurz und knapp", "zahlen und fakten", "infografik",
        ),
    },
    "infographic_timeline_hints": {
        "en": (
            "timeline", "time line", "history of", "milestones", "over the years",
            "chronology", "evolution of",
        ),
        "zh_tw": ("時間軸", "時間線", "發展歷程", "里程碑", "歷史沿革", "大事記"),
        "zh_cn": ("时间轴", "时间线", "发展历程", "里程碑", "历史沿革", "大事记"),
        "es": (
            "línea de tiempo", "linea de tiempo", "cronología", "cronologia",
            "hitos", "historia de", "a lo largo de los años", "evolución de",
            "evolucion de",
        ),
        "de": (
            "zeitleiste", "zeitachse", "chronologie", "meilensteine",
            "geschichte von", "im laufe der jahre", "entwicklung von",
        ),
    },
    "diagram_step_cues": {
        # Ordinal/sequence words that disambiguate a step-oriented "infographic"
        # toward the diagram renderer rather than the poster renderer.
        "en": ("step 1", "step one", "first,", "then,", "next,", "finally,", "stage 1"),
        "zh_tw": ("第一步", "步驟一", "首先", "接著", "然後", "最後"),
        "zh_cn": ("第一步", "步骤一", "首先", "接着", "然后", "最后"),
        "es": ("paso 1", "primero,", "luego,", "después,", "despues,", "finalmente,"),
        "de": ("schritt 1", "erstens,", "dann,", "danach,", "schließlich,", "schliesslich,"),
    },
    "content_only": {
        "en": ("just the content", "only the content"),
        "zh_tw": ("只要內容", "僅內容"),
        "zh_cn": ("只要内容", "仅内容"),
        "es": ("solo el contenido", "únicamente el contenido", "unicamente el contenido"),
        "de": ("nur der inhalt", "nur den inhalt"),
    },
    "quantity_words": {
        # Vague/indefinite plural quantifiers — "a few", "some", "several" — plus
        # number-words two..ten. Bare digits ("3") are locale-agnostic and matched
        # separately by callers via a plain \d regex, not listed here.
        "en": (
            "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
            "a few", "few", "some", "several", "multiple", "a couple", "couple of", "many",
        ),
        "zh_tw": ("兩個", "三個", "幾個", "一些", "幾種", "多個", "數個", "好幾個"),
        "zh_cn": ("两个", "三个", "几个", "一些", "几种", "多个", "数个", "好几个"),
        "es": (
            "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez",
            "unos pocos", "algunos", "algunas", "varios", "varias", "múltiples", "multiples",
            "un par de",
        ),
        "de": (
            "zwei", "drei", "vier", "fünf", "fuenf", "sechs", "sieben", "acht", "neun", "zehn",
            "ein paar", "einige", "mehrere", "mehrfach", "verschiedene",
        ),
    },
    "plural_item_nouns": {
        "en": ("translat", "version", "option", "alternativ", "way", "example", "point", "idea"),
        "zh_tw": ("翻譯", "版本", "選項", "方案", "方式", "例子", "例句", "重點", "想法"),
        "zh_cn": ("翻译", "版本", "选项", "方案", "方式", "例子", "例句", "重点", "想法"),
        "es": (
            "traduc", "versión", "version", "opción", "opcion", "alternativ", "manera",
            "ejemplo", "punto", "idea",
        ),
        "de": (
            "übersetz", "uebersetz", "version", "option", "alternativ", "möglichkeit",
            "moeglichkeit", "beispiel", "punkt", "idee",
        ),
    },
}

# Language names people ask to translate INTO — locale key -> {target language: display}.
_LANGUAGE_NAME_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "english": "en", "chinese": "zh_cn", "mandarin": "zh_cn", "traditional chinese": "zh_tw",
        "simplified chinese": "zh_cn", "spanish": "es", "german": "de", "french": "fr",
        "japanese": "ja", "korean": "ko",
    },
    "zh_tw": {
        "英語": "en", "英文": "en", "中文": "zh_cn", "繁體中文": "zh_tw", "繁體": "zh_tw",
        "簡體中文": "zh_cn", "西班牙語": "es", "德語": "de", "法語": "fr", "日語": "ja", "韓語": "ko",
    },
    "zh_cn": {
        "英语": "en", "英文": "en", "中文": "zh_cn", "繁体中文": "zh_tw", "繁体": "zh_tw",
        "简体中文": "zh_cn", "西班牙语": "es", "德语": "de", "法语": "fr", "日语": "ja", "韩语": "ko",
    },
    "es": {
        "inglés": "en", "ingles": "en", "chino": "zh_cn", "mandarín": "zh_cn", "mandarin": "zh_cn",
        "chino tradicional": "zh_tw", "chino simplificado": "zh_cn", "español": "es",
        "espanol": "es", "alemán": "de", "aleman": "de", "francés": "fr", "frances": "fr",
        "japonés": "ja", "japones": "ja", "coreano": "ko",
    },
    "de": {
        "englisch": "en", "chinesisch": "zh_cn", "mandarin": "zh_cn",
        "traditionelles chinesisch": "zh_tw", "vereinfachtes chinesisch": "zh_cn",
        "spanisch": "es", "deutsch": "de", "französisch": "fr", "franzoesisch": "fr",
        "japanisch": "ja", "koreanisch": "ko",
    },
}

_COLOR_WORD_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "blue": "blue", "red": "red", "green": "green", "yellow": "yellow", "orange": "orange",
        "purple": "purple", "pink": "pink", "white": "white", "black": "black", "brown": "brown",
        "gray": "gray", "grey": "gray", "rainbow": "rainbow",
    },
    "zh_tw": {
        "藍色": "blue", "藍": "blue", "紅色": "red", "紅": "red", "綠色": "green", "綠": "green",
        "黃色": "yellow", "黃": "yellow", "橙色": "orange", "紫色": "purple", "紫": "purple",
        "粉紅色": "pink", "粉色": "pink", "白色": "white", "白": "white", "黑色": "black",
        "黑": "black", "棕色": "brown", "灰色": "gray", "灰": "gray", "彩虹": "rainbow",
    },
    "zh_cn": {
        "蓝色": "blue", "蓝": "blue", "红色": "red", "红": "red", "绿色": "green", "绿": "green",
        "黄色": "yellow", "黄": "yellow", "橙色": "orange", "紫色": "purple", "紫": "purple",
        "粉红色": "pink", "粉色": "pink", "白色": "white", "白": "white", "黑色": "black",
        "黑": "black", "棕色": "brown", "灰色": "gray", "灰": "gray", "彩虹": "rainbow",
    },
    "es": {
        "azul": "blue", "rojo": "red", "verde": "green", "amarillo": "yellow",
        "naranja": "orange", "morado": "purple", "púrpura": "purple", "purpura": "purple",
        "rosa": "pink", "blanco": "white", "negro": "black", "marrón": "brown",
        "marron": "brown", "gris": "gray", "arcoíris": "rainbow", "arcoiris": "rainbow",
    },
    "de": {
        "blau": "blue", "rot": "red", "grün": "green", "gruen": "green", "gelb": "yellow",
        "orange": "orange", "lila": "purple", "violett": "purple", "rosa": "pink",
        "pink": "pink", "weiß": "white", "weiss": "white", "schwarz": "black",
        "braun": "brown", "grau": "gray", "regenbogen": "rainbow",
    },
}


# Requests to depict a person unclothed, phrased without the words "nude"/"naked" —
# used by pipeline/image_safety_embeddings.py's explicit-content gate on BOTH the user's own
# wording and the authored image prompt (a small authoring LLM rewrote "without clothes"
# into "no clothing or fabric visible", which no keyword/embedding check caught).
# Deliberately bodily/undress phrasings only — bare "nude"/"naked" (which also mean a
# lipstick colour or "naked eye") stay with the proximity check in that module.
CONCEPTS["explicit_nudity_request"] = {
    "en": (
        "without clothes", "without clothing", "without any clothes", "without any clothing",
        "no clothing or fabric", "no clothing visible", "no clothes visible", "wearing no clothes",
        "wearing no clothing", "not wearing clothes", "not wearing anything",
        "wearing nothing", "take off her clothes", "take off his clothes", "unclothed",
        "undressed", "topless woman", "topless girl", "topless lady", "topless model",
        "topless female", "topless man", "topless beach", "bottomless", "in the buff",
        "birthday suit", "stripped naked", "strip naked",
        "bare breasts", "bare-breasted",
    ),
    "zh_tw": (
        "沒穿衣服", "沒有穿衣服", "不穿衣服", "沒有衣服", "不著寸縷", "全裸", "裸體", "裸露",
        "赤裸", "脫光", "光著身子", "上空", "無衣",
    ),
    "zh_cn": (
        "没穿衣服", "没有穿衣服", "不穿衣服", "没有衣服", "不着寸缕", "全裸", "裸体", "裸露",
        "赤裸", "脱光", "光着身子", "上空", "无衣",
    ),
    "es": (
        "sin ropa", "sin vestir", "desnuda", "desnudo", "desnudas", "desnudos", "en pelotas",
        "semidesnud", "sin nada de ropa", "con los pechos al aire",
    ),
    "de": (
        "ohne kleidung", "ohne kleider", "ohne klamotten", "ohne etwas an", "unbekleidet",
        "entkleidet", "nackt", "oben ohne", "splitternackt",
    ),
}


def matches(query: str, concept: str) -> bool:
    """True if `query` contains any phrase for `concept`, in any supported locale."""
    table = CONCEPTS.get(concept)
    if not table:
        raise KeyError(f"Unknown query-intent concept: {concept!r}")
    lower = (query or "").lower()
    for locale in SUPPORTED_LOCALES:
        for phrase in table.get(locale, ()):
            if phrase.lower() in lower:
                return True
    return False


def find_first(query: str, concept: str) -> int | None:
    """Earliest character position of any `concept` phrase (any locale) in `query`,
    or None. Used for multi-intent ordering (e.g. "summarize this then translate it"
    -> summarizer before translator)."""
    table = CONCEPTS.get(concept)
    if not table:
        raise KeyError(f"Unknown query-intent concept: {concept!r}")
    lower = (query or "").lower()
    best: int | None = None
    for locale in SUPPORTED_LOCALES:
        for phrase in table.get(locale, ()):
            idx = lower.find(phrase.lower())
            if idx != -1 and (best is None or idx < best):
                best = idx
    return best


def matching_phrase(query: str, concept: str) -> str | None:
    """First matching phrase for `concept` across all locales, or None."""
    table = CONCEPTS.get(concept)
    if not table:
        raise KeyError(f"Unknown query-intent concept: {concept!r}")
    lower = (query or "").lower()
    for locale in SUPPORTED_LOCALES:
        for phrase in table.get(locale, ()):
            if phrase.lower() in lower:
                return phrase
    return None


def any_matches(query: str, concepts: tuple[str, ...]) -> bool:
    return any(matches(query, c) for c in concepts)


def find_color_word(instruction: str) -> str | None:
    """Canonical English color name (blue/red/.../rainbow) found anywhere in
    `instruction`, in any supported locale — or None."""
    lower = (instruction or "").lower()
    for locale in SUPPORTED_LOCALES:
        for phrase, canonical in _COLOR_WORD_PHRASES.get(locale, {}).items():
            if phrase.lower() in lower:
                return canonical
    return None


_PREP_BY_LOCALE = {
    "en": r"(?:to|into|as)",
    "es": r"(?:a|en)",
    "de": r"(?:zu|in|als)",
}


def find_color_word_after_preposition(instruction: str) -> str | None:
    """Canonical color name when introduced by a "to/into/as <color>"-shaped
    preposition (en/es/de use prepositions this way; zh_tw/zh_cn don't — a bare
    color word match via find_color_word() is used for those instead, since
    Chinese doesn't mark this with a preposition the same way)."""
    lower = (instruction or "").lower()
    for locale, prep in _PREP_BY_LOCALE.items():
        for phrase, canonical in _COLOR_WORD_PHRASES.get(locale, {}).items():
            if re.search(rf"\b{prep}\s+{re.escape(phrase.lower())}\b", lower):
                return canonical
    for phrase, canonical in {
        **_COLOR_WORD_PHRASES["zh_tw"], **_COLOR_WORD_PHRASES["zh_cn"],
    }.items():
        if phrase.lower() in lower:
            return canonical
    return None


def find_language_target(query: str) -> str | None:
    """Canonical language code (en/zh_tw/zh_cn/es/de/fr/ja/ko) the user asked to
    translate INTO, in any supported locale — or None."""
    lower = (query or "").lower()
    for locale in SUPPORTED_LOCALES:
        for phrase, canonical in _LANGUAGE_NAME_PHRASES.get(locale, {}).items():
            if phrase.lower() in lower:
                return canonical
    return None
