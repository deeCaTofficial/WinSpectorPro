import asyncio
import csv
import json
import logging
import os
import re
import subprocess
import sys
import winreg
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
def setup_logging(log_dir: Path, existing_log_file: Optional[str] = None):
    """Настраивает логирование в консоль и в файл (новый или существующий)."""
    log_dir.mkdir(exist_ok=True)
    log_file_path = None
    file_mode = 'a'
    if existing_log_file and Path(existing_log_file).exists():
        log_file_path = Path(existing_log_file)
    else:
        file_mode = 'w'
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        log_file_path = log_dir / f"researcher_run_{timestamp}.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    file_handler = logging.FileHandler(log_file_path, file_mode, 'utf-8')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(name)s:%(lineno)d] - %(message)s')
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = logging.Formatter('[%(levelname)s] - [%(name)s] - %(message)s')
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    return log_file_path
logger = logging.getLogger("ResearcherV3_Final")
try:
    import ctypes
    import google.generativeai as genai
    import psutil
    import yaml
    from dotenv import load_dotenv
    import win32api  # Для получения метаданных файла
    from src.winspector.core.modules import UserProfiler, WindowsOptimizer
except ImportError as e:
    logger.error(f"Критическая ошибка: не удалось импортировать модули. {e}")
    logger.error("Убедитесь, что установлены все зависимости, включая 'pywin32': pip install -r requirements-dev.txt")
    sys.exit(1)
CONFIG = {
    "AI_MODEL": 'gemini-2.5-flash-preview-05-20',
    "OUTPUT_PATH": PROJECT_ROOT / "tests" / "upload",
    "REQUEST_TIMEOUT": 600,
    "MAX_OUTPUT_TOKENS": 4194304,
    "VERIFICATION_BATCH_SIZE": 10,
    "CRITICAL_COMPONENTS_GUARDRAIL": {
        "services": ["Winmgmt", "RpcSs", "DcomLaunch", "PlugPlay"],
        "uwp_apps": ["Microsoft.WindowsStore"],
        "processes": ["csrss.exe", "wininit.exe", "lsass.exe", "svchost.exe", "explorer.exe"]
    }
}
class ResearcherV3_Final:
    def __init__(self):
        load_dotenv(PROJECT_ROOT / ".env")
        CONFIG["OUTPUT_PATH"].mkdir(exist_ok=True)
        self.model = self._initialize_ai()
        self.system_data_cache: Optional[Dict[str, Any]] = None
    def _initialize_ai(self) -> genai.GenerativeModel:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("API-ключ 'GEMINI_API_KEY' не найден.")
        genai.configure(api_key=api_key)
        logger.info(f"Инициализирован ИИ с моделью: {CONFIG['AI_MODEL']}")
        return genai.GenerativeModel(CONFIG['AI_MODEL'])
    def _get_file_metadata(self, path: str) -> Optional[Dict[str, str]]:
        try:
            path = os.path.expandvars(path.strip('"'))
            if not os.path.exists(path):
                return None
            info = win32api.GetFileVersionInfo(path, '\\')
            ms = info['FileVersionMS']
            ls = info['FileVersionLS']
            version = f"{win32api.HIWORD(ms)}.{win32api.LOWORD(ms)}.{win32api.HIWORD(ls)}.{win32api.LOWORD(ls)}"
            lang, codepage = win32api.GetFileVersionInfo(path, '\\VarFileInfo\\Translation')[0]
            str_info_path = f'\\StringFileInfo\\{lang:04x}{codepage:04x}\\'
            return {
                "company_name": win32api.GetFileVersionInfo(path, str_info_path + 'CompanyName'),
                "file_description": win32api.GetFileVersionInfo(path, str_info_path + 'FileDescription'),
                "product_version": win32api.GetFileVersionInfo(path, str_info_path + 'ProductVersion'),
                "file_version": version,
            }
        except Exception:
            return None
    def _get_raw_startup_items(self) -> List[Dict[str, str]]:
        startup_items = []
        startup_folders = [
            os.path.join(os.getenv('APPDATA', ''), 'Microsoft\\Windows\\Start Menu\\Programs\\Startup'),
            os.path.join(os.getenv('ALLUSERSPROFILE', ''), 'Microsoft\\Windows\\Start Menu\\Programs\\Startup')
        ]
        for folder in startup_folders:
            if os.path.isdir(folder):
                for item in os.listdir(folder):
                    startup_items.append({"source": "Startup Folder", "name": item, "command": os.path.join(folder, item)})
        run_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Run"),
        ]
        for hkey, path in run_keys:
            try:
                with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ) as key:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                            startup_items.append({"source": f"Registry ({path})", "name": name, "command": value})
                            i += 1
                        except OSError:
                            break
            except FileNotFoundError:
                continue
        return startup_items
    def _collect_startup_items(self) -> List[Dict[str, Any]]:
        logger.info("Сбор элементов автозагрузки с метаданными...")
        items = self._get_raw_startup_items()
        for item in items:
            command_path = item.get("command", "").split(" ")[0]
            item["metadata"] = self._get_file_metadata(command_path)
        return items
    def _collect_scheduled_tasks(self) -> List[Dict[str, Any]]:
        logger.info("Сбор запланированных задач с метаданными...")
        tasks = []
        try:
            cmd = ['schtasks', '/query', '/fo', 'CSV', '/v']
            proc = subprocess.run(cmd, capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
            csv_data = proc.stdout.decode(sys.getfilesystemencoding(), errors='ignore')
            reader = csv.DictReader(csv_data.splitlines())
            for row in reader:
                task_name = row.get('TaskName')
                author = row.get('Author')
                if task_name and author and 'Microsoft' not in author and 'Window' not in author:
                    command = row.get('Task To Run', "")
                    command_path = command.split(" ")[0]
                    tasks.append({
                        "name": task_name,
                        "author": author,
                        "command": command,
                        "metadata": self._get_file_metadata(command_path),
                        "status": row.get('Status')
                    })
        except Exception as e:
            logger.error(f"Не удалось получить список запланированных задач: {e}")
        return tasks
    def _collect_hosts_file_entries(self) -> List[str]:
        logger.info("Анализ файла hosts...")
        hosts_path = Path(os.environ.get("WINDIR", "C:\\Windows")) / "System32" / "drivers" / "etc" / "hosts"
        entries = []
        if not hosts_path.exists():
            return entries
        try:
            with open(hosts_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if '127.0.0.1' in line and 'localhost' in line:
                            continue
                        if '::1' in line and 'localhost' in line:
                            continue
                        entries.append(line)
        except Exception as e:
            logger.error(f"Не удалось прочитать файл hosts: {e}")
        return entries
    async def _collect_dynamic_data(self) -> Dict[str, Any]:
        logger.info("Сбор динамических данных (процессы и сеть)...")
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'username', 'exe', 'cmdline']):
            if not proc.info.get('username') or proc.info['username'] in ['NT AUTHORITY\\SYSTEM', 'NT AUTHORITY\\LOCAL SERVICE'] or not proc.info.get('exe'):
                continue
            proc_info = proc.info
            proc_info['metadata'] = self._get_file_metadata(proc_info['exe'])
            processes.append(proc_info)
        net_connections = []
        try:
            for conn in psutil.net_connections(kind='inet'):
                if conn.status == 'ESTABLISHED' and conn.raddr:
                    try:
                        proc = psutil.Process(conn.pid)
                        net_connections.append({
                            'pid': conn.pid,
                            'process_name': proc.name(),
                            'local_address': f"{conn.laddr.ip}:{conn.laddr.port}",
                            'remote_address': f"{conn.raddr.ip}:{conn.raddr.port}"
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        except psutil.AccessDenied:
            logger.warning("Нет прав администратора для сбора сетевых соединений.")
        return {"running_processes": processes, "network_connections": net_connections}
    async def _get_system_data(self) -> Dict[str, Any]:
        if self.system_data_cache:
            return self.system_data_cache
        logger.info("Начало полного сбора системных данных (ResearcherV3_Final)...")
        profiler = UserProfiler(profiler_config={})
        optimizer = WindowsOptimizer()
        profile_task = profiler.get_system_profile()
        components_task = optimizer.get_system_components()
        dynamic_task = self._collect_dynamic_data()
        startup_items = self._collect_startup_items()
        scheduled_tasks = self._collect_scheduled_tasks()
        hosts_entries = self._collect_hosts_file_entries()
        logger.info("Поиск точных путей установки для ключевых программ...")
        steam_path = self._get_install_path_from_registry("Steam")
        known_paths = {}
        if steam_path:
            known_paths["steam_install_path"] = steam_path
            logger.info(f"Найден путь установки Steam: {steam_path}")
        profile, components, dynamic = await asyncio.gather(profile_task, components_task, dynamic_task)
        self.system_data_cache = {
            "profile": profile,
            "components": components,
            "dynamic": dynamic,
            "startup_items": startup_items,
            "scheduled_tasks": scheduled_tasks,
            "hosts_file_entries": hosts_entries,
            "known_install_paths": known_paths
        }
        logger.info("Полный сбор системных данных завершен.")
        return self.system_data_cache
    async def _generate_from_prompt(self, prompt: str, task_name: str) -> Dict:
            logger.info(f"Отправка запроса ИИ для задачи: '{task_name}'...")
            generation_config = genai.types.GenerationConfig(
            max_output_tokens=CONFIG["MAX_OUTPUT_TOKENS"],
            temperature=0.2
        )
            request_options = {"timeout": CONFIG["REQUEST_TIMEOUT"]}
            try:
                response = await self.model.generate_content_async(
                    prompt,
                    generation_config=generation_config,
                    request_options=request_options
                )
                return self._parse_and_restore_json(response.text, task_name)
            except Exception as e:
                logger.critical(f"Критическая ошибка API при запросе для '{task_name}': {e}", exc_info=True)
                return {}
    def _parse_and_restore_json(self, json_text: str, task_name: str) -> Dict:
            try:
                match = re.search(r'```json\s*(\{.*\}|\[.*\])\s*```', json_text, re.DOTALL)
                clean_json_text = match.group(1) if match else json_text
                return json.loads(clean_json_text)
            except json.JSONDecodeError as e:
                logger.warning(f"Получен невалидный JSON для '{task_name}'. Ошибка: {e}. Пытаемся спасти части данных...")
                root_key_match = re.search(r'"([^"]+)"\s*:\s*\[', clean_json_text)
                if not root_key_match:
                    logger.error(f"Не удалось найти корневой ключ в оборванном JSON для '{task_name}'.")
                    return {}
                root_key = root_key_match.group(1)
                saved_items = []
                for match in re.finditer(r'\{[^{}]*\}', clean_json_text):
                    try:
                        saved_items.append(json.loads(match.group(0)))
                    except json.JSONDecodeError:
                        continue
                if saved_items:
                    logger.info(f"Удалось спасти {len(saved_items)} полных объектов из оборванного JSON для '{task_name}'.")
                    return {root_key: saved_items}
                else:
                    logger.error(f"Не удалось восстановить ни одного объекта из JSON для '{task_name}'.\nПолный ответ ИИ:\n{json_text}")
                    return {}
            except Exception as e:
                logger.error(f"Непредвиденная ошибка при парсинге JSON для '{task_name}': {e}\nПолный ответ ИИ:\n{json_text}")
                return {}
    def _create_knowledge_base_prompt(self, data: Dict, previous_results: List[Dict]) -> str:
            data_for_prompt = {'components': data.get('components', {}), 'startup_items': data.get('startup_items', []), 'scheduled_tasks': data.get('scheduled_tasks', []), 'hosts_file_entries': data.get('hosts_file_entries', [])}
            draft_section = self._get_draft_section(previous_results)
            thinking_instructions = """
            **Your thought process MUST follow these steps before generating the final JSON:**
            1.  **Analyze Services:** Go through the list of services. For each one, ask: "Is this service non-essential for a typical user? Is it telemetry? Is it safe to disable? What are the consequences?" Cross-reference with the critical components guardrail.
            2.  **Analyze Startup & Tasks:** Look at startup items and scheduled tasks, paying close attention to their `metadata` (CompanyName, FileDescription). Ask: "What is the purpose of this program? Is it necessary for it to run at startup/on schedule? Is it a helper, updater, or bloatware?"
            3.  **Synthesize Rules:** Based on your analysis from steps 1-2, formulate the JSON objects for the rules you have identified as new and relevant.
            4.  **Final JSON Output:** Present ONLY the final, complete JSON object containing the list of new rules. Do not include your thought process in the final output.
            """
            return f"""
            You are a senior system analyst. Your task is to analyze system data and identify optimization candidates.
            
            {thinking_instructions}
            
            {draft_section}

            Guardrail: NEVER suggest disabling components from this critical list: {json.dumps(CONFIG['CRITICAL_COMPONENTS_GUARDRAIL'])}.
            For each NEW candidate, provide: `id`, `type`, `safety`, `relevant_profiles`, `description_ru`, and a `provenance` object with `added_by: "ai_suggestion_v3.8"`.

            System Data: {json.dumps(data_for_prompt, indent=2, default=str)}
            Respond with a single JSON object with a key "optimization_candidates" containing ONLY NEWLY IDENTIFIED rules.
            """
    def _create_telemetry_domains_prompt(self, data: Dict, previous_results: List[Dict]) -> str:
            data_for_prompt = {'dynamic': data.get('dynamic', {}), 'hosts_file_entries': data.get('hosts_file_entries', [])}
            draft_section = self._get_draft_section(previous_results)
            thinking_instructions = """
            **Your thought process MUST follow these steps before generating the final JSON:**
            1.  **Correlate Data:** For each network connection, identify the parent process (`process_name`) and its metadata (`CompanyName`).
            2.  **Identify Candidates:** Look for connections where the process name (e.g., `NVIDIATelemetryContainer.exe`) or company name (e.g., `Yandex LLC`) clearly indicates a non-essential service, telemetry, or advertising.
            3.  **Formulate Rules:** For each identified candidate, create a rule for the most specific, blockable subdomain possible (e.g., `telemetry.nvidia.com`, not `nvidia.com`). Assess the `breakage_risk`.
            4.  **Final JSON Output:** Present ONLY the final, complete JSON object.
            """
            return f"""
            You are a senior security analyst specializing in privacy. Your task is to analyze network data to identify domains used **SPECIFICALLY for telemetry, user tracking, crash reporting, and advertising.**
            
            {thinking_instructions}

            **IMPORTANT RULES:**
            1.  **BE EXTREMELY SPECIFIC.** Do not suggest top-level domains like `google.com`.
            2.  **AVOID FUNCTIONAL and INFRASTRUCTURE DOMAINS** (e.g., CDNs, core APIs).

            {draft_section}

            For each NEW domain you identify as **non-essential and telemetry-related**, provide: `domain`, `description_ru`, `category`, `breakage_risk`, and `provenance` with `added_by: "ai_suggestion_v3.8"`.

            System Data: {json.dumps(data_for_prompt, indent=2, default=str)}
            Respond with a single JSON object with a key "telemetry_domains" containing ONLY NEWLY IDENTIFIED rules.
            """
    def _create_cleanup_rules_prompt(self, data: Dict, previous_results: List[Dict]) -> str:
            data_for_prompt = {'installed_software': data.get("profile", {}).get("installed_software", []), 'known_install_paths': data.get('known_install_paths', {})}
            draft_section = self._get_draft_section(previous_results)
            thinking_instructions = """
            **Your thought process MUST follow these steps before generating the final JSON:**
            1.  **Review Software List:** Go through the list of installed software.
            2.  **Brainstorm Paths:** For each major application (e.g., 'Steam', 'NVIDIA Driver', 'VSCode'), recall the typical locations where it stores cache, logs, and temporary files. Use environment variables like `%LOCALAPPDATA%` and `%APPDATA%`.
            3.  **Use Known Paths:** If a specific installation path is provided in `known_install_paths`, use it as a base for constructing the rule paths.
            4.  **Formulate Rules:** Create a rule for each identified cleanup category. Define the `cleanup_type` based on whether you are cleaning a whole directory or specific files by mask.
            5.  **Final JSON Output:** Present ONLY the final, complete JSON object.
            """
            return f"""
            You are a senior optimization engineer. Your task is to create rules for cleaning temporary files and caches. **Your ONLY task is to identify FOLDERS and FILES for deletion. DO NOT suggest uninstalling programs.**
            
            {thinking_instructions}

            {draft_section}

            For each NEW rule, provide: `category_id`, `description_ru`, `cleanup_type`, `paths`, `safety`, etc, and `provenance` with `added_by: "ai_suggestion_v3.8"`.

            System Data: {json.dumps(data_for_prompt, indent=2, default=str)}
            Respond with a single JSON object with a key "cleanup_rules" containing ONLY NEWLY IDENTIFIED rules.
            """
    def _get_draft_section(self, previous_results: List[Dict]) -> str:
        if not previous_results:
            return "This is the first pass. Please generate the initial set of rules."
        else:
            return f"""
            This is a refinement pass. We have already found these rules.
            **YOUR MAIN GOAL IS TO FIND WHAT WAS MISSED. Do NOT repeat any entries from this list:**
            ```json
            {json.dumps(previous_results, indent=2, default=str)}
            ```
            """
    def _create_verification_prompt(self, item_to_verify: Dict, item_id: str) -> str:
            item_type = item_to_verify.get('type', 'item')
            description = item_to_verify.get('description_ru', 'No description provided.')
            thinking_instructions = """
            **Your thought process MUST follow these steps before giving the final JSON:**
            1.  **Deconstruct the Statement:** Break down the core claims in the description. What is the component's function? What is the recommended action? What are the stated consequences?
            2.  **Fact-Check:** Access your internal knowledge base. Is the stated function of the component correct? Are the consequences of the action accurately described?
            3.  **Assess Safety:** Evaluate the safety of the recommendation for a general, non-expert user. Is there a significant risk of breaking something important?
            4.  **Formulate Conclusion:** Based on your analysis, decide if the statement is `is_correct` (meaning both accurate and safe). Formulate a `confidence` score and a concise `correction_comment` if needed.
            5.  **Final JSON Output:** After completing your internal thought process, provide ONLY the final, complete JSON object as requested below.
            """
            return f"""
            You are a meticulous fact-checker AI. Your task is to verify a single statement about a Windows system component.
            
            {thinking_instructions}

            **STATEMENT TO VERIFY:**
            A {item_type} named "{item_id}" has the following description and recommendation: "{description}"

            **YOUR TASKS:**
            1.  Based on your extensive knowledge, is this statement accurate?
            2.  Is the recommendation safe and reasonable for a general user?
            3.  Provide a confidence score for your verification.

            **RESPONSE FORMAT:**
            You MUST respond with a single, valid JSON object with the following keys:
            - `is_correct`: boolean (true if the statement is generally accurate and safe, false otherwise).
            - `confidence`: float (a number from 0.0 to 1.0 representing your confidence in the verification).
            - `correction_comment`: string (If `is_correct` is false, provide a brief explanation of the error. If true, you can provide a small refinement or leave it empty).

            **Example for a correct statement:**
            ```json
            {{
                "is_correct": true,
                "confidence": 0.95,
                "correction_comment": "The description is accurate. It could be mentioned that this also affects Xbox Game Bar recordings."
            }}
            ```

            **Example for an incorrect statement:**
            ```json
            {{
                "is_correct": false,
                "confidence": 0.99,
                "correction_comment": "This statement is incorrect. The 'bthserv' service is critical for all Bluetooth functionality, not just audio. Disabling it will break all Bluetooth devices."
            }}
            ```
            """
    def _create_synthesis_prompt(self, all_rules: Dict) -> str:
            data_for_prompt = {
                "knowledge_base": all_rules.get("knowledge_base", []),
                "telemetry_domains": all_rules.get("telemetry_domains", []),
                "cleanup_rules": all_rules.get("cleanup_rules", [])
            }
            thinking_instructions = """
            **Your thought process MUST follow these steps before generating the final JSON:**
            1.  **Identify Key Entities:** Create a mental map of key software entities mentioned across all rule sets (e.g., 'NVIDIA', 'Steam', 'Yandex Browser', 'VSCode').
            2.  **Service-to-Domain Mapping:** For each entity, look for a service/startup item in `knowledge_base` and a corresponding domain in `telemetry_domains`.
            3.  **App-to-Cleanup Mapping:** For each entity, look for its presence in `knowledge_base` (as a service or startup item) and a corresponding rule in `cleanup_rules`.
            4.  **Formulate Insights:** For every significant link you find, formulate a "synthesis_note" object explaining the connection. Prioritize clear, actionable insights.
            5.  **Final JSON Output:** Present ONLY the final, complete JSON object.
            """
            return f"""
            You are a lead architect AI. Your task is to synthesize and find cross-references between different sets of system optimization rules.

            {thinking_instructions}

            **INPUT DATA (three sets of rules generated previously):**
            ```json
            {json.dumps(data_for_prompt, indent=2, default=str)}
            ```
            
            **YOUR TASK (reiteration):**
            Based on the connections you found during your thought process, generate a new list of "synthesis_notes".
            Each note should be an object with keys: `insight_id`, `type`, `primary_rule_id`, `related_rule_ids`, `comment_ru`.

            **RESPONSE FORMAT:**
            Respond with a single JSON object with a single key "synthesis_notes", containing a list of the objects you generate.
            If you find no meaningful connections, return an empty list.
            """
    def _get_install_path_from_registry(self, display_name: str) -> Optional[str]:
        uninstall_keys = [
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
            r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
        ]
        for path in uninstall_keys:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            subkey_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, subkey_name) as subkey:
                                try:
                                    name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                    if display_name.lower() in name.lower():
                                        install_location = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                                        return install_location
                                except (FileNotFoundError, OSError):
                                    continue
                        except OSError:
                            continue
            except FileNotFoundError:
                continue
        return None
    async def _run_iterative_generation_for(self, category_name: str, system_data: Dict) -> List[Dict]:
        logger.info(f"--- НАЧАЛО ИТЕРАТИВНОЙ ГЕНЕРАЦИИ ДЛЯ: {category_name.upper()} ---")
        wave_number = 1
        accumulated_items = []
        if category_name == "knowledge_base":
            prompt_func = self._create_knowledge_base_prompt
            root_key = "optimization_candidates"
            id_key = "id"
        elif category_name == "telemetry_domains":
            prompt_func = self._create_telemetry_domains_prompt
            root_key = "telemetry_domains"
            id_key = "domain"
        else:
            prompt_func = self._create_cleanup_rules_prompt
            root_key = "cleanup_rules"
            id_key = "category_id"
        while True:
            logger.info(f"[{category_name.upper()}] Запуск волны №{wave_number}...")
            prompt = prompt_func(system_data, accumulated_items)
            new_json = await self._generate_from_prompt(prompt, f"{category_name} Wave {wave_number}")
            new_items_list = []
            if isinstance(new_json, dict):
                new_items_list = new_json.get(root_key, [])
            elif isinstance(new_json, list):
                logger.warning(f"ИИ вернул список напрямую для '{root_key}', а не словарь. Обрабатываем как есть.")
                new_items_list = new_json
            if not isinstance(new_items_list, list):
                new_items_list = []
            if not new_items_list:
                logger.info(f"[{category_name.upper()}] ИИ не нашел новых правил на волне №{wave_number}. Завершение.")
                break
            existing_ids = {item.get(id_key) for item in accumulated_items}
            added_count = 0
            for item in new_items_list:
                if isinstance(item, dict) and item.get(id_key) not in existing_ids:
                    if item.get("type") == "service":
                         match = re.search(r'(_[a-f0-9]{5,})$', item[id_key])
                         if match:
                            prefix = item[id_key][:match.start()]
                            item[id_key] = f"{prefix}_*"
                            item["match_by"] = "prefix"
                    if item.get(id_key) not in existing_ids:
                        accumulated_items.append(item)
                        existing_ids.add(item.get(id_key))
                        added_count += 1
            logger.info(f"[{category_name.upper()}] Результаты волны №{wave_number}: Добавлено {added_count} новых правил.")
            if added_count == 0:
                logger.info(f"[{category_name.upper()}] Новых уникальных правил не найдено. Завершение.")
                break
            wave_number += 1
            if wave_number > 25:
                logger.warning(f"[{category_name.upper()}] Достигнут лимит в 25 итераций. Принудительное завершение.")
                break
        logger.info(f"--- ГЕНЕРАЦИЯ ДЛЯ {category_name.upper()} ЗАВЕРШЕНА. Всего найдено: {len(accumulated_items)} правил. ---")
        return accumulated_items
    async def run(self):
        try:
            system_data = await self._get_system_data()
            kb_items = await self._run_iterative_generation_for("knowledge_base", system_data)
            td_items = await self._run_iterative_generation_for("telemetry_domains", system_data)
            cr_items = await self._run_iterative_generation_for("cleanup_rules", system_data)
            logger.info("--- ЗАПУСК ФИНАЛЬНОЙ ВОЛНЫ СИНТЕЗА ---")
            synthesis_prompt = self._create_synthesis_prompt({
                "knowledge_base": kb_items,
                "telemetry_domains": td_items,
                "cleanup_rules": cr_items
            })
            synthesis_json = await self._generate_from_prompt(synthesis_prompt, "Synthesis Wave")
            all_generated_items = kb_items + td_items + cr_items
            verified_items = await self._run_verification_wave(all_generated_items)
            final_kb = [item for item in verified_items if item.get('type') in ["service", "uwp_app", "startup_item", "hosts_entry"]]
            final_td = [item for item in verified_items if "breakage_risk" in item]
            final_cr = [item for item in verified_items if "cleanup_type" in item]
            logger.info("--- ФИНАЛЬНОЕ СОХРАНЕНИЕ ВЕРИФИЦИРОВАННЫХ ПРАВИЛ ---")
            self._validate_and_save_final_yaml(final_kb, "knowledge_base.yaml")
            self._validate_and_save_final_yaml(final_td, "telemetry_domains.yaml")
            self._validate_and_save_final_yaml(final_cr, "smart_cleanup_rules.yaml")
            if synthesis_json:
                self._validate_and_save_final_yaml(synthesis_json.get("synthesis_notes", []), "synthesis_notes.yaml", validate_paths=False)
            logger.info("\nИтеративное исследование и верификация завершены!")
        except Exception as e:
            logger.critical(f"Произошла непредвиденная ошибка в процессе исследования: {e}", exc_info=True)
    async def _run_verification_wave(self, items_to_verify: List[Dict]) -> List[Dict]:
        if not items_to_verify:
            return []
        batch_size = CONFIG["VERIFICATION_BATCH_SIZE"]
        logger.info(f"--- НАЧАЛО ВОЛНЫ ВЕРИФИКАЦИИ для {len(items_to_verify)} правил (пачками по {batch_size}) ---")
        all_verified_items = []
        item_batches = [items_to_verify[i:i + batch_size] for i in range(0, len(items_to_verify), batch_size)]
        for i, batch in enumerate(item_batches):
            logger.info(f"Обработка пачки верификации №{i + 1} из {len(item_batches)} ({len(batch)} правил)...")
            verification_tasks = []
            for item in batch:
                id_key = 'domain' if 'breakage_risk' in item else 'category_id' if 'cleanup_type' in item else 'id'
                item_id = item.get(id_key, 'unknown')
                prompt = self._create_verification_prompt(item, item_id)
                task = self._generate_from_prompt(prompt, f"Verification for '{item_id}'")
                verification_tasks.append(task)
            verification_results = await asyncio.gather(*verification_tasks)
            for j, verification_json in enumerate(verification_results):
                original_item = batch[j]
                id_key = 'domain' if 'breakage_risk' in original_item else 'category_id' if 'cleanup_type' in original_item else 'id'
                item_id = original_item.get(id_key, 'unknown')
                if verification_json and verification_json.get("is_correct") is True:
                    if "provenance" not in original_item or not isinstance(original_item.get("provenance"), dict):
                        original_item["provenance"] = {}
                    original_item["provenance"]["verified_by"] = "ai_peer_review"
                    original_item["provenance"]["verification_confidence"] = verification_json.get("confidence", 0.0)
                    if verification_json.get("correction_comment"):
                        original_item["provenance"]["verification_comment"] = verification_json["correction_comment"]
                    all_verified_items.append(original_item)
                else:
                    comment = verification_json.get("correction_comment", "No comment provided.")
                    logger.warning(f"Правило для '{item_id}' не прошло верификацию и было отброшено. Комментарий ИИ: '{comment}'")
            if i < len(item_batches) - 1:
                logger.info(f"Пачка №{i + 1} обработана. Пауза на 60 секунд...")
                await asyncio.sleep(60)
        logger.info(f"Верификация завершена. {len(all_verified_items)} из {len(items_to_verify)} правил прошли проверку.")
        return all_verified_items
    def _validate_and_save_final_yaml(self, data: List[Dict], filename: str, validate_paths: bool = True):
            output_file = CONFIG["OUTPUT_PATH"] / filename
            if not data:
                logger.info(f"Нет данных для сохранения в '{filename}'. Файл не будет изменен.")
                return
            validated_data = []
            is_cleanup_rule = 'cleanup' in filename and validate_paths
            if not is_cleanup_rule:
                validated_data = data
            else:
                for item in data:
                    if "paths" in item:
                        if not any(os.path.exists(os.path.expandvars(p)) for p in item.get("paths", [])):
                            logger.warning(f"Правило '{item.get('category_id')}' отфильтровано: ни один из путей не существует. Пути: {item.get('paths')}")
                            continue
                    validated_data.append(item)
            if not validated_data:
                logger.info(f"После валидации не осталось правил для сохранения в '{filename}'.")
                return
            with open(output_file, "w", encoding="utf-8") as f:
                yaml.dump(validated_data, f, allow_unicode=True, sort_keys=False, indent=2)
            logger.info(f"✅ Валидированный файл '{output_file}' с {len(validated_data)} правилами успешно сохранен.")
    @staticmethod
    def is_admin() -> bool:
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except AttributeError:
            return False
    @staticmethod
    def run_as_admin(log_file_to_pass: Path):
        logger.info("Требуются права администратора. Попытка перезапуска...")
        try:
            params = f'"{sys.argv[0]}" --log-file "{log_file_to_pass}"'
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        except Exception as e:
            logger.error(f"Не удалось выполнить перезапуск: {e}")
    async def main_async():
        researcher = ResearcherV3_Final()
        await researcher.run()
if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    log_file_arg = None
    if "--log-file" in sys.argv:
        try:
            index = sys.argv.index("--log-file")
            log_file_arg = sys.argv.pop(index + 1)
            sys.argv.pop(index)
        except (ValueError, IndexError):
            print("Ошибка: аргумент --log-file указан без пути.", file=sys.stderr)
    log_file = setup_logging(CONFIG["OUTPUT_PATH"], existing_log_file=log_file_arg)
    logger.info(f"Логи этого сеанса сохраняются в: {log_file}")
    logger.info(f"Текущая рабочая директория: {os.getcwd()}")
    if sys.platform == "win32":
        if not ResearcherV3_Final.is_admin():
            ResearcherV3_Final.run_as_admin(log_file)
            sys.exit(0)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        researcher_instance = ResearcherV3_Final()
        asyncio.run(researcher_instance.run())
    except KeyboardInterrupt:
        logger.info("\nПроцесс прерван пользователем.")
    except Exception as e:
        logger.critical(f"Критический сбой при запуске: {e}", exc_info=True)