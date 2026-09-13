# -*- coding: utf-8 -*-
"""Localized extension catalog titles and descriptions."""
from __future__ import annotations

from pipeline.i18n import get_locale, normalize_locale

# ext_id -> {title, description}
_CATALOG: dict[str, dict[str, dict[str, str]]] = {
    "en": {
        "document_editor": {
            "title": "Document Editor",
            "description": "View, edit, export, and highlight Office documents and PDFs.",
        },
        "web_viewer": {
            "title": "Web Viewer",
            "description": "Browse pages and highlight content for LOMA.",
        },
        "chat_archive_manager": {
            "title": "Chat Archive",
            "description": "Save and reload conversation archives.",
        },
        "email_assistant": {
            "title": "Email Assistant",
            "description": "Gmail inbox, read, send, and drafts (OAuth).",
        },
        "formslator": {
            "title": "Formslator",
            "description": "Form translation with glossary and style mapping.",
        },
        "research": {
            "title": "Research",
            "description": "Guided research with clarify step, web and file sources, and exportable reports.",
        },
        "artwork_studio": {
            "title": "Artwork Studio",
            "description": "Generate artwork with style presets, local diffusion, and workspace export.",
        },
        "token_tracker": {
            "title": "Token Usage",
            "description": "Track LLM token usage per model, totals, and custom date ranges.",
        },
        "news_brief": {
            "title": "News Brief",
            "description": "Filter news by category, region, and topic, and summarize the brief.",
        },
        "history_events": {
            "title": "History Events",
            "description": "Real historical encounters — answer one question, then get graded A–F.",
        },
        "logic_puzzle": {
            "title": "Logic Puzzle",
            "description": "Reasoning puzzles with graded answers and full solutions.",
        },
        "arena": {
            "title": "Arena",
            "description": "Pit LLM personas or models against each other — compete or cooperate.",
        },
        "document_intelligence": {
            "title": "Document Intelligence",
            "description": "Workspace and library document tasks — Search, Ask, Analyze.",
        },
        "data_studio": {
            "title": "Data Studio",
            "description": "Upload multiple spreadsheets, clean and correlate them, and explore an interactive dashboard with overlays, analysis, and Q&A.",
        },
        "profile_manager": {
            "title": "Profile Manager",
            "description": "Manage LOMA profiles and preferences.",
        },
    },
    "zh_tw": {
        "document_editor": {
            "title": "文件編輯器",
            "description": "檢視、編輯、匯出及標註 Office 文件與 PDF。",
        },
        "web_viewer": {
            "title": "網頁檢視器",
            "description": "瀏覽網頁並標註內容供 LOMA 使用。",
        },
        "chat_archive_manager": {
            "title": "對話封存",
            "description": "儲存並重新載入對話紀錄。",
        },
        "email_assistant": {
            "title": "電子郵件助理",
            "description": "Gmail 收件匣、閱讀、傳送與草稿（OAuth）。",
        },
        "formslator": {
            "title": "表單翻譯器",
            "description": "表單翻譯，含詞彙表與樣式對應。",
        },
        "research": {
            "title": "研究",
            "description": "引導式研究：釐清需求、網頁與檔案來源、可匯出報告。",
        },
        "artwork_studio": {
            "title": "美術工作室",
            "description": "以風格預設與本機擴散模型產生圖像並匯出。",
        },
        "token_tracker": {
            "title": "Token 用量",
            "description": "依模型、總計與自訂日期範圍追蹤 LLM Token 用量。",
        },
        "news_brief": {
            "title": "新聞摘要",
            "description": "依類別、地區與主題篩選新聞並摘要。",
        },
        "history_events": {
            "title": "歷史事件",
            "description": "真實歷史情境 — 回答問題後獲得 A–F 評分。",
        },
        "logic_puzzle": {
            "title": "邏輯謎題",
            "description": "推理謎題，含評分答案與完整解析。",
        },
        "arena": {
            "title": "競技場",
            "description": "讓 LLM 角色或模型互相競爭或合作。",
        },
        "document_intelligence": {
            "title": "文件智慧",
            "description": "工作區與資料庫文件任務 — 搜尋、提問、分析。",
        },
        "data_studio": {
            "title": "資料工作室",
            "description": "上傳多份試算表、清理與關聯，並以互動儀表板探索疊加、分析與問答。",
        },
        "profile_manager": {
            "title": "設定檔管理",
            "description": "管理 LOMA 設定檔與偏好。",
        },
    },
    "zh_cn": {
        "document_editor": {
            "title": "文档编辑器",
            "description": "查看、编辑、导出及标注 Office 文档与 PDF。",
        },
        "web_viewer": {
            "title": "网页查看器",
            "description": "浏览网页并标注内容供 LOMA 使用。",
        },
        "chat_archive_manager": {
            "title": "对话存档",
            "description": "保存并重新加载对话记录。",
        },
        "email_assistant": {
            "title": "电子邮件助手",
            "description": "Gmail 收件箱、阅读、发送与草稿（OAuth）。",
        },
        "formslator": {
            "title": "表单翻译器",
            "description": "表单翻译，含词汇表与样式映射。",
        },
        "research": {
            "title": "研究",
            "description": "引导式研究：澄清需求、网页与文件来源、可导出报告。",
        },
        "artwork_studio": {
            "title": "美术工作室",
            "description": "以风格预设与本地扩散模型生成图像并导出。",
        },
        "token_tracker": {
            "title": "Token 用量",
            "description": "按模型、总计与自定义日期范围追踪 LLM Token 用量。",
        },
        "news_brief": {
            "title": "新闻摘要",
            "description": "按类别、地区与主题筛选新闻并摘要。",
        },
        "history_events": {
            "title": "历史事件",
            "description": "真实历史情境 — 回答问题后获得 A–F 评分。",
        },
        "logic_puzzle": {
            "title": "逻辑谜题",
            "description": "推理谜题，含评分答案与完整解析。",
        },
        "arena": {
            "title": "竞技场",
            "description": "让 LLM 角色或模型互相竞争或合作。",
        },
        "document_intelligence": {
            "title": "文档智能",
            "description": "工作区与库文档任务 — 搜索、提问、分析。",
        },
        "data_studio": {
            "title": "数据工作室",
            "description": "上传多份电子表格、清理与关联，并以互动仪表板探索叠加、分析与问答。",
        },
        "profile_manager": {
            "title": "配置文件管理",
            "description": "管理 LOMA 配置文件与偏好。",
        },
    },
    "es": {
        "document_editor": {
            "title": "Editor de documentos",
            "description": "Vea, edite, exporte y resalte documentos de Office y PDF.",
        },
        "web_viewer": {
            "title": "Visor web",
            "description": "Explore páginas y resalte contenido para LOMA.",
        },
        "chat_archive_manager": {
            "title": "Archivo de chat",
            "description": "Guarde y vuelva a cargar archivos de conversaciones.",
        },
        "email_assistant": {
            "title": "Asistente de correo",
            "description": "Bandeja de entrada de Gmail, lectura, envío y borradores (OAuth).",
        },
        "formslator": {
            "title": "Formslator",
            "description": "Traducción de formularios con glosario y correspondencia de estilo.",
        },
        "research": {
            "title": "Investigación",
            "description": "Investigación guiada con paso de aclaración, fuentes web y de archivos, e informes exportables.",
        },
        "artwork_studio": {
            "title": "Estudio de arte",
            "description": "Genere obras de arte con estilos predefinidos, difusión local y exportación al espacio de trabajo.",
        },
        "token_tracker": {
            "title": "Uso de tokens",
            "description": "Rastree el uso de tokens de LLM por modelo, totales y rangos de fechas personalizados.",
        },
        "news_brief": {
            "title": "Resumen de noticias",
            "description": "Filtre noticias por categoría, región y tema, y resuma el informe.",
        },
        "history_events": {
            "title": "Eventos históricos",
            "description": "Encuentros históricos reales — responda una pregunta y reciba una calificación de A a F.",
        },
        "logic_puzzle": {
            "title": "Rompecabezas lógico",
            "description": "Acertijos de razonamiento con respuestas calificadas y soluciones completas.",
        },
        "arena": {
            "title": "Arena",
            "description": "Enfrente personajes o modelos de LLM entre sí — compitan o cooperen.",
        },
        "document_intelligence": {
            "title": "Inteligencia de documentos",
            "description": "Tareas de documentos del espacio de trabajo y la biblioteca — Buscar, Preguntar, Analizar.",
        },
        "data_studio": {
            "title": "Estudio de datos",
            "description": "Cargue varias hojas de cálculo, límpielas y correlaciónelas, y explore un panel interactivo con superposiciones, análisis y preguntas y respuestas.",
        },
        "profile_manager": {
            "title": "Gestor de perfiles",
            "description": "Gestione los perfiles y preferencias de LOMA.",
        },
    },
    "de": {
        "document_editor": {
            "title": "Dokumenteneditor",
            "description": "Office-Dokumente und PDFs anzeigen, bearbeiten, exportieren und markieren.",
        },
        "web_viewer": {
            "title": "Web-Viewer",
            "description": "Seiten durchsuchen und Inhalte für LOMA markieren.",
        },
        "chat_archive_manager": {
            "title": "Chat-Archiv",
            "description": "Unterhaltungsarchive speichern und erneut laden.",
        },
        "email_assistant": {
            "title": "E-Mail-Assistent",
            "description": "Gmail-Posteingang, Lesen, Senden und Entwürfe (OAuth).",
        },
        "formslator": {
            "title": "Formslator",
            "description": "Formularübersetzung mit Glossar und Stilzuordnung.",
        },
        "research": {
            "title": "Recherche",
            "description": "Geführte Recherche mit Klärungsschritt, Web- und Dateiquellen sowie exportierbaren Berichten.",
        },
        "artwork_studio": {
            "title": "Kunststudio",
            "description": "Erstellen Sie Kunstwerke mit Stilvorgaben, lokaler Diffusion und Export in den Arbeitsbereich.",
        },
        "token_tracker": {
            "title": "Token-Nutzung",
            "description": "Verfolgen Sie die LLM-Token-Nutzung pro Modell, Summen und benutzerdefinierte Zeiträume.",
        },
        "news_brief": {
            "title": "Nachrichtenüberblick",
            "description": "Nachrichten nach Kategorie, Region und Thema filtern und zusammenfassen.",
        },
        "history_events": {
            "title": "Historische Ereignisse",
            "description": "Reale historische Begegnungen — beantworten Sie eine Frage und erhalten Sie eine Note von A bis F.",
        },
        "logic_puzzle": {
            "title": "Logikrätsel",
            "description": "Denksportaufgaben mit bewerteten Antworten und vollständigen Lösungen.",
        },
        "arena": {
            "title": "Arena",
            "description": "Lassen Sie LLM-Personas oder -Modelle gegeneinander antreten — wetteifern oder kooperieren.",
        },
        "document_intelligence": {
            "title": "Dokumentintelligenz",
            "description": "Dokumentaufgaben für Arbeitsbereich und Bibliothek — Suchen, Fragen, Analysieren.",
        },
        "data_studio": {
            "title": "Daten-Studio",
            "description": "Laden Sie mehrere Tabellenkalkulationen hoch, bereinigen und korrelieren Sie sie und erkunden Sie ein interaktives Dashboard mit Overlays, Analysen und Fragen und Antworten.",
        },
        "profile_manager": {
            "title": "Profilverwaltung",
            "description": "LOMA-Profile und -Einstellungen verwalten.",
        },
    },
}


def extension_title(ext_id: str, fallback: str = "") -> str:
    loc = get_locale()
    entry = (_CATALOG.get(loc) or _CATALOG["en"]).get(ext_id) or _CATALOG["en"].get(ext_id)
    if entry and entry.get("title"):
        return entry["title"]
    return fallback or ext_id.replace("_", " ").title()


def extension_description(ext_id: str, fallback: str = "") -> str:
    loc = get_locale()
    entry = (_CATALOG.get(loc) or _CATALOG["en"]).get(ext_id) or _CATALOG["en"].get(ext_id)
    if entry and entry.get("description"):
        return entry["description"]
    return fallback


def localized_category(raw: str) -> str:
    from pipeline.i18n import t

    key = normalize_library_category(raw)
    return t(f"library.category_{key}")


def normalize_library_category(raw: str) -> str:
    from services.plugins.extension_categories import normalize_library_category as _norm

    return _norm(raw)
