"""
Audit script for raw Telegram JSON exports in data/raw/
Scans exported JSON files to inspect schema, fields, ID formats, reactions, and volume.
"""

import os
import json
import glob
from pathlib import Path
from datetime import datetime

RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"

def audit_telegram_export(file_path: Path):
    print(f"\n==================================================")
    print(f"Анализ файла: {file_path.name}")
    print(f"Размер: {file_path.stat().st_size / (1024*1024):.2f} MB")
    print(f"==================================================")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Ошибка чтения JSON: {e}")
        return

    chat_id = data.get("id")
    chat_name = data.get("name")
    chat_type = data.get("type")
    messages = data.get("messages", [])

    print(f"Чат ID: {chat_id}")
    print(f"Название: {chat_name}")
    print(f"Тип: {chat_type}")
    print(f"Всего сообщений в файле: {len(messages):,}")

    if not messages:
        print("В файле нет сообщений.")
        return

    # Статистика по полям
    has_from_id = 0
    has_from = 0
    has_reply = 0
    has_reactions = 0
    has_views = 0
    has_forwards = 0
    has_text = 0
    has_entities = 0

    user_ids = set()
    from_id_patterns = set()
    min_date = None
    max_date = None

    for msg in messages:
        # Дата
        date_str = msg.get("date")
        if date_str:
            if not min_date or date_str < min_date:
                min_date = date_str
            if not max_date or date_str > max_date:
                max_date = date_str

        # from_id
        fid = msg.get("from_id")
        if fid:
            has_from_id += 1
            user_ids.add(fid)
            # сохраняем префикс (user, channel, etc)
            if isinstance(fid, str):
                prefix = fid[:4] if fid.startswith(("user", "chan")) else "other_str"
                from_id_patterns.add(prefix)
            else:
                from_id_patterns.add("integer")

        if msg.get("from"):
            has_from += 1
        if msg.get("reply_to_message_id"):
            has_reply += 1
        if msg.get("reactions"):
            has_reactions += 1
        if msg.get("views") is not None:
            has_views += 1
        if msg.get("forwarded_from"):
            has_forwards += 1

        text = msg.get("text")
        if text:
            has_text += 1
        if msg.get("text_entities"):
            has_entities += 1

    total = len(messages)
    print(f"\nДиапазон дат: с {min_date} по {max_date}")
    print(f"Уникальных авторов (from_id): {len(user_ids):,}")
    print(f"Форматы from_id: {list(from_id_patterns)}")
    print(f"\nПокрытие полей (% от всех сообщений):")
    print(f"  - from_id: {has_from_id}/{total} ({has_from_id/total*100:.1f}%)")
    print(f"  - from (имя): {has_from}/{total} ({has_from/total*100:.1f}%)")
    print(f"  - reply_to_message_id: {has_reply}/{total} ({has_reply/total*100:.1f}%)")
    print(f"  - text: {has_text}/{total} ({has_text/total*100:.1f}%)")
    print(f"  - text_entities (ссылки, теги): {has_entities}/{total} ({has_entities/total*100:.1f}%)")
    print(f"  - reactions: {has_reactions}/{total} ({has_reactions/total*100:.1f}%)")
    print(f"  - views: {has_views}/{total} ({has_views/total*100:.1f}%)")
    print(f"  - forwarded_from: {has_forwards}/{total} ({has_forwards/total*100:.1f}%)")

def main():
    json_files = list(RAW_DIR.glob("*.json"))
    if not json_files:
        print(f"Папка {RAW_DIR} пуста. Поместите файлы JSON выгрузок в data/raw/")
        return

    print(f"Найдено файлов для аудита: {len(json_files)}")
    for f in json_files:
        audit_telegram_export(f)

if __name__ == "__main__":
    main()
