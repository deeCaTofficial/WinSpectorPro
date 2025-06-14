# src/winspector/core/config.py
"""
Файл с резервной (fallback) конфигурацией для модулей ядра.

Эти конфигурации используются только в том случае, если соответствующие
секции отсутствуют в файле `knowledge_base.yaml`. Основным источником
конфигурации всегда является YAML-файл, что позволяет настраивать
приложение без изменения кода.
"""

# ===================================================================
# Резервная конфигурация для UserProfiler
# ===================================================================
# Используется, если секция 'user_profiler_config' не найдена
# в knowledge_base.yaml.
DEFAULT_USER_PROFILER_CONFIG = {
    # Ключевые слова для поиска в названиях установленных программ.
    # Помогают ИИ подтвердить профиль пользователя.
    "app_keywords": {
        "Gamer": [
            "steam", "epic games", "gog galaxy", "battle.net", "origin",
            "uplay", "geforce", "radeon", "discord", "obs studio", "msi afterburner"
        ],
        "Developer": [
            "visual studio", "vscode", "docker", "python", "java", "node.js",
            "android studio", "pycharm", "jetbrains", "git", "kubernetes", "postman"
        ],
        "Designer": [
            "photoshop", "illustrator", "figma", "sketch", "after effects",
            "premiere pro", "blender", "autocad", "coreldraw", "cinema 4d"
        ],
        "OfficeWorker": [
            "office", "excel", "word", "powerpoint", "outlook", "teams",
            "slack", "zoom", "1c", "sap"
        ],
    },
    
    # Файловые маркеры: ключевые папки, указывающие на определенный
    # тип деятельности. Пути указаны относительно %USERPROFILE%.
    "filesystem_markers": {
        "Gamer": [
            "\\Steam\\steamapps",
            "\\Epic Games\\Launcher",
            "\\GOG Galaxy\\Games",
            "\\Battle.net"
        ],
        "Developer": [
            "\\source\\repos",  # Visual Studio
            "\\.docker",
            "\\.vscode",
            "\\JetBrains"
        ],
        "Designer": [
            "\\Creative Cloud Files",
            "\\.figma"
        ]
    }
}

# Можно добавить другие резервные конфигурации здесь
# DEFAULT_SMART_CLEANER_CONFIG = { ... }