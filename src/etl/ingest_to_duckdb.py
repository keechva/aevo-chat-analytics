"""
High-performance incremental ETL: Ingest raw Telegram JSON exports into DuckDB.
Uses PyArrow / Polars columnar loading for sub-second ingestion of 250k+ messages.
"""

import os
import json
from pathlib import Path
from datetime import datetime
import duckdb
import pyarrow as pa
from tqdm import tqdm

BASE_DIR = Path(__file__).resolve().parent.parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
DB_PATH = BASE_DIR / "data" / "processed" / "analytics.duckdb"

def extract_plain_text(raw_text):
    if not raw_text:
        return ""
    if isinstance(raw_text, str):
        return raw_text
    if isinstance(raw_text, list):
        parts = []
        for item in raw_text:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("text", ""))
        return "".join(parts)
    return str(raw_text)

def init_db(con: duckdb.DuckDBPyConnection):
    con.execute("""
    CREATE TABLE IF NOT EXISTS dim_chats (
        chat_id BIGINT PRIMARY KEY,
        chat_name VARCHAR,
        chat_type VARCHAR,
        first_message_date TIMESTAMP,
        last_message_date TIMESTAMP,
        total_messages BIGINT
    );
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS fact_messages (
        chat_id BIGINT,
        message_id BIGINT,
        user_id VARCHAR,
        display_name VARCHAR,
        date TIMESTAMP,
        date_unixtime BIGINT,
        text VARCHAR,
        reply_to_message_id BIGINT,
        reactions_count INTEGER,
        views INTEGER,
        forwards INTEGER,
        has_entities BOOLEAN,
        PRIMARY KEY (chat_id, message_id)
    );
    """)

    con.execute("""
    CREATE TABLE IF NOT EXISTS dim_users (
        user_id VARCHAR PRIMARY KEY,
        display_name VARCHAR,
        is_channel BOOLEAN,
        first_seen TIMESTAMP,
        last_seen TIMESTAMP,
        total_messages BIGINT
    );
    """)

def process_file(file_path: Path, con: duckdb.DuckDBPyConnection):
    print(f"\nProcessing {file_path.name}...")
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chat_id = data.get("id")
    chat_name = data.get("name", "Unknown")
    chat_type = data.get("type", "unknown")
    raw_messages = data.get("messages", [])

    if not raw_messages:
        print("No messages.")
        return

    # Prepare columnar lists
    chat_ids = []
    message_ids = []
    user_ids = []
    display_names = []
    dates = []
    date_unixtimes = []
    texts = []
    reply_to_message_ids = []
    reactions_counts = []
    views_list = []
    forwards_list = []
    has_entities_list = []

    min_date = None
    max_date = None

    for m in raw_messages:
        if m.get("type") != "message":
            continue

        msg_id = m.get("id")
        d_str = m.get("date")
        if not d_str:
            continue

        # normalize timestamp string to ISO
        try:
            dt = datetime.fromisoformat(d_str)
        except Exception:
            continue

        if not min_date or d_str < min_date:
            min_date = d_str
        if not max_date or d_str > max_date:
            max_date = d_str

        d_unix = int(m.get("date_unixtime", 0)) if m.get("date_unixtime") else int(dt.timestamp())
        fid = m.get("from_id")
        fname = m.get("from")
        reply_id = m.get("reply_to_message_id")
        views = int(m.get("views", 0)) if m.get("views") else 0
        forwards = int(m.get("forwards", 0)) if m.get("forwards") else 0

        r_count = 0
        reactions = m.get("reactions")
        if reactions and isinstance(reactions, list):
            for r in reactions:
                if isinstance(r, dict):
                    r_count += r.get("count", 1)

        txt = extract_plain_text(m.get("text"))
        has_ent = bool(m.get("text_entities"))

        chat_ids.append(chat_id)
        message_ids.append(msg_id)
        user_ids.append(fid)
        display_names.append(fname)
        dates.append(dt)
        date_unixtimes.append(d_unix)
        texts.append(txt)
        reply_to_message_ids.append(reply_id)
        reactions_counts.append(r_count)
        views_list.append(views)
        forwards_list.append(forwards)
        has_entities_list.append(has_ent)

    print(f"Extracted {len(message_ids):,} valid messages. Creating PyArrow Table...")

    schema = pa.schema([
        ("chat_id", pa.int64()),
        ("message_id", pa.int64()),
        ("user_id", pa.string()),
        ("display_name", pa.string()),
        ("date", pa.timestamp("us")),
        ("date_unixtime", pa.int64()),
        ("text", pa.string()),
        ("reply_to_message_id", pa.int64()),
        ("reactions_count", pa.int32()),
        ("views", pa.int32()),
        ("forwards", pa.int32()),
        ("has_entities", pa.bool_())
    ])

    arrow_table = pa.Table.from_arrays([
        pa.array(chat_ids, type=pa.int64()),
        pa.array(message_ids, type=pa.int64()),
        pa.array(user_ids, type=pa.string()),
        pa.array(display_names, type=pa.string()),
        pa.array(dates, type=pa.timestamp("us")),
        pa.array(date_unixtimes, type=pa.int64()),
        pa.array(texts, type=pa.string()),
        pa.array(reply_to_message_ids, type=pa.int64()),
        pa.array(reactions_counts, type=pa.int32()),
        pa.array(views_list, type=pa.int32()),
        pa.array(forwards_list, type=pa.int32()),
        pa.array(has_entities_list, type=pa.bool_())
    ], schema=schema)

    # Fast bulk insert / upsert via DuckDB temporary view
    con.register("temp_batch", arrow_table)
    con.execute("""
    INSERT OR REPLACE INTO fact_messages (
        chat_id, message_id, user_id, display_name,
        date, date_unixtime, text, reply_to_message_id,
        reactions_count, views, forwards, has_entities
    ) SELECT * FROM temp_batch;
    """)
    con.unregister("temp_batch")

    # Update chat metadata
    con.execute("""
    INSERT INTO dim_chats (chat_id, chat_name, chat_type, first_message_date, last_message_date, total_messages)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT (chat_id) DO UPDATE SET
        chat_name = EXCLUDED.chat_name,
        chat_type = EXCLUDED.chat_type,
        first_message_date = EXCLUDED.first_message_date,
        last_message_date = EXCLUDED.last_message_date,
        total_messages = EXCLUDED.total_messages;
    """, (chat_id, chat_name, chat_type, min_date, max_date, len(message_ids)))

    print(f"Chat '{chat_name}' loaded successfully.")

def update_user_dimensions(con: duckdb.DuckDBPyConnection):
    print("\nAggregating dim_users across all messages...")
    con.execute("""
    INSERT OR REPLACE INTO dim_users (
        user_id, display_name, is_channel, first_seen, last_seen, total_messages
    )
    SELECT
        user_id,
        FIRST(display_name) as display_name,
        BOOL_OR(user_id LIKE 'channel%') as is_channel,
        MIN(date) as first_seen,
        MAX(date) as last_seen,
        COUNT(*) as total_messages
    FROM fact_messages
    WHERE user_id IS NOT NULL
    GROUP BY user_id;
    """)

def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    init_db(con)

    json_files = list(RAW_DIR.glob("*.json"))
    print(f"Found {len(json_files)} dumps to ingest into DuckDB...")

    start_t = datetime.now()
    for f in sorted(json_files, key=lambda x: x.stat().st_size):
        process_file(f, con)

    update_user_dimensions(con)

    # Indices
    con.execute("CREATE INDEX IF NOT EXISTS idx_msg_date ON fact_messages(date);")
    con.execute("CREATE INDEX IF NOT EXISTS idx_msg_user ON fact_messages(user_id);")

    duration = (datetime.now() - start_t).total_seconds()

    chat_count = con.execute("SELECT count(*) FROM dim_chats").fetchone()[0]
    user_count = con.execute("SELECT count(*) FROM dim_users").fetchone()[0]
    msg_count = con.execute("SELECT count(*) FROM fact_messages").fetchone()[0]

    print("\n==========================================")
    print(f"Ingestion Completed in {duration:.2f} seconds!")
    print(f"Database: {DB_PATH}")
    print(f"Total Chats: {chat_count}")
    print(f"Total Unique Users: {user_count:,}")
    print(f"Total Messages: {msg_count:,}")
    print("==========================================")
    con.close()

if __name__ == "__main__":
    main()
