"""
FastAPI backend for Product Discovery & Chat Analytics.
Exposes REST endpoints for stats, signal filtering, problem clusters, and hypotheses.
Supports multi-country filtering and opportunity scoring.
"""

import time
import uuid
import json
from typing import Optional, List
from pathlib import Path
from fastapi import APIRouter, Query, HTTPException, BackgroundTasks
from pydantic import BaseModel
import duckdb

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "processed" / "analytics.duckdb"

api_router = APIRouter(prefix="/api")

def get_db(read_only: bool = True):
    for attempt in range(5):
        try:
            return duckdb.connect(str(DB_PATH), read_only=read_only)
        except duckdb.IOException:
            time.sleep(0.15)
    return duckdb.connect(str(DB_PATH), read_only=read_only)

# Pydantic models
class HypothesisCreate(BaseModel):
    title: str
    topic: str
    target_audience: Optional[str] = "Экспаты и туристы в Таиланде"
    pain_summary: str
    proposed_solution: Optional[str] = ""
    sample_message_ids: Optional[str] = ""

class MessageTagUpdate(BaseModel):
    topic_category: str
    signal_type: str
    pain_intensity: float
    human_notes: Optional[str] = ""

class RuleCreate(BaseModel):
    rule_key: str
    rule_name: str
    topic: str
    keywords: List[str]
    signal_type: str
    default_pain: float

# 1. Countries Endpoint
@api_router.get("/countries")
def get_countries():
    con = get_db(read_only=True)
    rows = con.execute("""
    SELECT c.country_code, c.country_name, c.flag_emoji, c.is_active,
           count(DISTINCT ch.chat_id) as chats_count,
           count(m.message_id) as messages_count
    FROM dim_countries c
    LEFT JOIN dim_chats ch ON c.country_code = ch.country_code
    LEFT JOIN fact_messages m ON ch.chat_id = m.chat_id
    GROUP BY c.country_code, c.country_name, c.flag_emoji, c.is_active
    ORDER BY c.is_active DESC, messages_count DESC;
    """).fetchall()
    con.close()

    return [{
        "country_code": r[0],
        "country_name": r[1],
        "flag_emoji": r[2],
        "is_active": r[3],
        "chats_count": r[4] or 0,
        "messages_count": r[5] or 0
    } for r in rows]

# 2. Problem Clusters & Opportunity Scores
@api_router.get("/clusters")
def get_clusters(country_code: Optional[str] = "TH"):
    con = get_db(read_only=True)
    cond = f"WHERE country_code = '{country_code}'" if country_code and country_code != "all" else ""

    query = f"""
    SELECT
        cluster_id, country_code, topic, title, description,
        keywords, opportunity_score, mentions_count, avg_pain,
        commercial_intent_pct, sample_quotes
    FROM dim_problem_clusters
    {cond}
    ORDER BY opportunity_score DESC, mentions_count DESC;
    """
    rows = con.execute(query).fetchall()
    con.close()

    clusters = []
    for r in rows:
        quotes = []
        try:
            quotes = json.loads(r[10]) if r[10] else []
        except Exception:
            quotes = []

        clusters.append({
            "cluster_id": r[0],
            "country_code": r[1],
            "topic": r[2],
            "title": r[3],
            "description": r[4],
            "keywords": r[5] or [],
            "opportunity_score": r[6],
            "mentions_count": r[7],
            "avg_pain": r[8],
            "commercial_intent_pct": r[9],
            "sample_quotes": quotes
        })

    return clusters

# 3. Stats Endpoint
@api_router.get("/stats")
def get_stats(country_code: Optional[str] = "TH"):
    con = get_db(read_only=True)
    c_filter = f"WHERE m.country_code = '{country_code}'" if country_code and country_code != "all" else ""
    c_filter_and = f"AND m.country_code = '{country_code}'" if country_code and country_code != "all" else ""
    chat_filter = f"WHERE c.country_code = '{country_code}'" if country_code and country_code != "all" else ""

    total_messages = con.execute(f"SELECT count(*) FROM fact_messages m {c_filter}").fetchone()[0]
    total_users = con.execute(f"SELECT count(DISTINCT user_id) FROM fact_messages m WHERE user_id IS NOT NULL {c_filter_and}").fetchone()[0]

    classified_count = con.execute(f"""
    SELECT count(*) FROM fact_messages m 
    WHERE topic_category IS NOT NULL AND topic_category != '' {c_filter_and}
    """).fetchone()[0]

    high_pain_count = con.execute(f"""
    SELECT count(*) FROM fact_messages m 
    WHERE pain_intensity >= 2.0 {c_filter_and}
    """).fetchone()[0]

    commercial_count = con.execute(f"""
    SELECT count(*) FROM fact_messages m 
    WHERE signal_type = 'commercial_intent' {c_filter_and}
    """).fetchone()[0]

    # Topics breakdown
    topics = con.execute(f"""
    SELECT 
        COALESCE(topic_category, 'unclassified') as topic,
        count(*) as count,
        round(avg(COALESCE(pain_intensity, 0)), 2) as avg_pain
    FROM fact_messages m
    WHERE (date >= '2026-01-01' OR topic_category != 'unclassified') {c_filter_and}
    GROUP BY topic_category
    ORDER BY count DESC;
    """).fetchall()

    # Signals breakdown
    signals = con.execute(f"""
    SELECT 
        COALESCE(signal_type, 'unclassified') as signal,
        count(*) as count
    FROM fact_messages m
    WHERE (date >= '2026-01-01' OR signal_type != 'unclassified') {c_filter_and}
    GROUP BY signal_type
    ORDER BY count DESC;
    """).fetchall()

    # Chats breakdown
    chats = con.execute(f"""
    SELECT 
        c.chat_id,
        c.chat_name,
        COALESCE(c.total_messages, 0) as total_msgs
    FROM dim_chats c
    {chat_filter}
    ORDER BY total_msgs DESC;
    """).fetchall()

    con.close()

    return {
        "country_code": country_code,
        "total_messages": total_messages,
        "total_users": total_users,
        "classified_count": classified_count,
        "high_pain_count": high_pain_count,
        "commercial_count": commercial_count,
        "topics": [{"topic": r[0], "count": r[1], "avg_pain": r[2]} for r in topics],
        "signals": [{"signal": r[0], "count": r[1]} for r in signals],
        "chats": [{"chat_id": r[0], "chat_name": r[1], "count": r[2]} for r in chats]
    }

# 4. Signals Feed Endpoint
@api_router.get("/signals")
def get_signals(
    country_code: Optional[str] = "TH",
    topic: Optional[str] = None,
    signal_type: Optional[str] = None,
    min_pain: Optional[float] = None,
    chat_id: Optional[int] = None,
    search: Optional[str] = None,
    sort_by: str = "pain_desc",
    page: int = 1,
    limit: int = 30
):
    con = get_db(read_only=True)
    offset = (page - 1) * limit

    conditions = ["m.text IS NOT NULL", "length(trim(m.text)) > 8"]

    if country_code and country_code != "all":
        conditions.append(f"m.country_code = '{country_code}'")
    if topic and topic != "all":
        conditions.append(f"m.topic_category = '{topic}'")
    if signal_type and signal_type != "all":
        conditions.append(f"m.signal_type = '{signal_type}'")
    if min_pain is not None and min_pain > 0:
        conditions.append(f"m.pain_intensity >= {min_pain}")
    if chat_id:
        conditions.append(f"m.chat_id = {chat_id}")
    if search and search.strip():
        safe_q = search.replace("'", "''").lower()
        conditions.append(f"lower(m.text) LIKE '%{safe_q}%'")

    where_clause = " WHERE " + " AND ".join(conditions)

    order_clause = "ORDER BY m.pain_intensity DESC NULLS LAST, m.date DESC"
    if sort_by == "date_desc":
        order_clause = "ORDER BY m.date DESC"
    elif sort_by == "reactions_desc":
        order_clause = "ORDER BY m.reactions_count DESC, m.date DESC"

    # Count total
    total_count = con.execute(f"SELECT count(*) FROM fact_messages m {where_clause}").fetchone()[0]

    # Fetch page
    query = f"""
    SELECT
        m.chat_id,
        c.chat_name,
        m.message_id,
        m.user_id,
        m.display_name,
        strftime(m.date, '%Y-%m-%d %H:%M') as date_str,
        m.text,
        m.reactions_count,
        m.reply_to_message_id,
        COALESCE(m.topic_category, 'general') as topic_category,
        COALESCE(m.signal_type, 'casual_chat') as signal_type,
        COALESCE(m.pain_intensity, 0.0) as pain_intensity,
        COALESCE(m.human_verified, FALSE) as human_verified,
        COALESCE(m.human_notes, '') as human_notes
    FROM fact_messages m
    LEFT JOIN dim_chats c ON m.chat_id = c.chat_id
    {where_clause}
    {order_clause}
    LIMIT {limit} OFFSET {offset};
    """

    rows = con.execute(query).fetchall()
    con.close()

    items = []
    for r in rows:
        items.append({
            "chat_id": r[0],
            "chat_name": r[1],
            "message_id": r[2],
            "user_id": r[3],
            "display_name": r[4] or "Anonymous",
            "date": r[5],
            "text": r[6],
            "reactions_count": r[7] or 0,
            "reply_to_message_id": r[8],
            "topic_category": r[9],
            "signal_type": r[10],
            "pain_intensity": r[11],
            "human_verified": r[12],
            "human_notes": r[13]
        })

    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "items": items
    }

# 5. Hypotheses Endpoints
@api_router.get("/hypotheses")
def get_hypotheses():
    con = get_db(read_only=True)
    rows = con.execute("""
    SELECT
        id, title, topic, target_audience, pain_summary,
        evidence_count, sample_message_ids, status,
        strftime(created_at, '%Y-%m-%d %H:%M') as created_at
    FROM dim_hypotheses
    ORDER BY created_at DESC;
    """).fetchall()
    con.close()

    return [{
        "id": r[0],
        "title": r[1],
        "topic": r[2],
        "target_audience": r[3],
        "pain_summary": r[4],
        "evidence_count": r[5],
        "sample_message_ids": r[6],
        "status": r[7],
        "created_at": r[8]
    } for r in rows]

@api_router.post("/hypotheses")
def create_hypothesis(payload: HypothesisCreate):
    con = get_db(read_only=False)
    hyp_id = str(uuid.uuid4())[:8]
    con.execute("""
    INSERT INTO dim_hypotheses (
        id, title, topic, target_audience, pain_summary, sample_message_ids, status
    ) VALUES (?, ?, ?, ?, ?, ?, 'new');
    """, (hyp_id, payload.title, payload.topic, payload.target_audience, payload.pain_summary, payload.sample_message_ids))
    con.close()
    return {"status": "ok", "id": hyp_id}

@api_router.delete("/hypotheses/{hyp_id}")
def delete_hypothesis(hyp_id: str):
    con = get_db(read_only=False)
    con.execute("DELETE FROM dim_hypotheses WHERE id = ?", (hyp_id,))
    con.close()
    return {"status": "ok"}

# 6. Rules & Tagging
@api_router.get("/rules")
def get_rules():
    con = get_db(read_only=True)
    rows = con.execute("""
    SELECT rule_key, rule_name, topic, keywords, signal_type, default_pain, is_active
    FROM dim_rules
    ORDER BY rule_name;
    """).fetchall()
    con.close()

    return [{
        "rule_key": r[0],
        "rule_name": r[1],
        "topic": r[2],
        "keywords": r[3] if r[3] else [],
        "signal_type": r[4],
        "default_pain": r[5],
        "is_active": r[6]
    } for r in rows]

@api_router.post("/messages/{chat_id}/{message_id}/tag")
def update_message_tag(chat_id: int, message_id: int, payload: MessageTagUpdate):
    con = get_db(read_only=False)
    con.execute("""
    UPDATE fact_messages
    SET
        topic_category = ?,
        signal_type = ?,
        pain_intensity = ?,
        human_notes = ?,
        human_verified = TRUE
    WHERE chat_id = ? AND message_id = ?;
    """, (payload.topic_category, payload.signal_type, payload.pain_intensity, payload.human_notes, chat_id, message_id))
    con.close()
    return {"status": "ok"}

@api_router.post("/classify/trigger")
def trigger_classify(background_tasks: BackgroundTasks, limit: int = 500):
    from src.classifiers.laya_service import run_classification_pipeline
    background_tasks.add_task(run_classification_pipeline, batch_size=64, limit=limit)
    return {"status": "started", "limit": limit}
