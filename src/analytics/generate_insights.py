"""
Comprehensive analytical report generator for 4 Telegram chat exports (240k messages).
Answers all 4 business questions + recommendations for 6 chats & @visadtv.
"""

import duckdb
from pathlib import Path
from collections import defaultdict

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "processed" / "analytics.duckdb"

def run_analytics():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    print("=" * 60)
    print("      AEVO CHAT ANALYTICS: ОТЧЕТ ПО 4 ЧАТАМ ТАИЛАНДА")
    print("=" * 60)

    # 1. ОБЩИЙ ОБЗОР
    print("\n--- 1. ОБЩИЙ ОБЗОР ЧАТОВ В БАЗЕ ---")
    chats_df = con.execute("""
    SELECT
        chat_id,
        chat_name,
        chat_type,
        strftime(first_message_date, '%Y-%m-%d') as first_msg,
        strftime(last_message_date, '%Y-%m-%d') as last_msg,
        total_messages
    FROM dim_chats
    ORDER BY total_messages DESC;
    """).pl()
    print(chats_df)

    total_msgs = con.execute("SELECT count(*) FROM fact_messages").fetchone()[0]
    total_users = con.execute("SELECT count(DISTINCT user_id) FROM fact_messages WHERE user_id IS NOT NULL").fetchone()[0]
    print(f"\nВсего сообщений в DuckDB: {total_msgs:,}")
    print(f"Всего уникальных участников (user_id): {total_users:,}")

    # 2. ЗАДАЧА 1: СКВОЗНАЯ ИДЕНТИФИКАЦИЯ И ПЕРЕСЕЧЕНИЯ
    print("\n" + "=" * 60)
    print("ЗАДАЧА 1: ИДЕНТИФИКАЦИЯ И СКВОЗНОЙ ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ")
    print("=" * 60)

    overlap_dist = con.execute("""
    WITH user_chats AS (
        SELECT user_id, count(DISTINCT chat_id) as n_chats
        FROM fact_messages
        WHERE user_id IS NOT NULL AND user_id LIKE 'user%'
        GROUP BY user_id
    )
    SELECT
        n_chats,
        count(*) as users_count,
        round(count(*) * 100.0 / sum(count(*)) over(), 2) as pct
    FROM user_chats
    GROUP BY n_chats
    ORDER BY n_chats;
    """).pl()
    print("\nРаспределение пользователей по числу чатов присутствия:")
    print(overlap_dist)

    print("\nМатрица парного пересечения аудитории между чатами:")
    pairwise = con.execute("""
    WITH u1 AS (
        SELECT DISTINCT user_id, chat_id
        FROM fact_messages
        WHERE user_id LIKE 'user%'
    )
    SELECT
        c1.chat_name as chat_A,
        c2.chat_name as chat_B,
        count(DISTINCT u1.user_id) as shared_users
    FROM u1
    JOIN u1 as u2 ON u1.user_id = u2.user_id AND u1.chat_id < u2.chat_id
    JOIN dim_chats c1 ON u1.chat_id = c1.chat_id
    JOIN dim_chats c2 ON u2.chat_id = c2.chat_id
    GROUP BY chat_A, chat_B
    ORDER BY shared_users DESC;
    """).pl()
    print(pairwise)

    # 3. ЗАДАЧА 2: СЕГМЕНТАЦИЯ АУДИТОРИИ (АКТИВНОСТЬ, РОЛИ Q&A, ЯДРО)
    print("\n" + "=" * 60)
    print("ЗАДАЧА 2: СЕГМЕНТАЦИЯ АУДИТОРИИ")
    print("=" * 60)

    activity_seg = con.execute("""
    WITH user_activity AS (
        SELECT
            user_id,
            count(*) as msg_cnt,
            datediff('day', min(date), max(date)) as lifespan_days
        FROM fact_messages
        WHERE user_id LIKE 'user%'
        GROUP BY user_id
    )
    SELECT
        CASE
            WHEN msg_cnt = 1 THEN '1. One-timers (1 сообщение)'
            WHEN msg_cnt BETWEEN 2 AND 5 THEN '2. Rare (2-5 сообщений)'
            WHEN msg_cnt BETWEEN 6 AND 20 THEN '3. Occasional (6-20 сообщений)'
            WHEN msg_cnt BETWEEN 21 AND 100 THEN '4. Active (21-100 сообщений)'
            ELSE '5. Core / Super-Active (>100 сообщений)'
        END as segment,
        count(*) as users_count,
        round(count(*) * 100.0 / sum(count(*)) over(), 2) as pct_users,
        sum(msg_cnt) as total_messages,
        round(sum(msg_cnt) * 100.0 / sum(sum(msg_cnt)) over(), 2) as pct_messages
    FROM user_activity
    GROUP BY segment
    ORDER BY segment;
    """).pl()
    print("\nСегментация пользователей по частоте и активности:")
    print(activity_seg)

    # Роли Q&A: Вопрошающие vs Отвечающие
    print("\nРоли участников (вопросы vs ответы):")
    roles = con.execute("""
    SELECT
        round(count(CASE WHEN reply_to_message_id IS NOT NULL THEN 1 END) * 100.0 / count(*), 1) as reply_msgs_pct,
        round(count(CASE WHEN reply_to_message_id IS NULL AND text LIKE '%?%' THEN 1 END) * 100.0 / count(*), 1) as direct_questions_pct,
        round(count(CASE WHEN reply_to_message_id IS NULL AND text NOT LIKE '%?%' THEN 1 END) * 100.0 / count(*), 1) as statements_pct
    FROM fact_messages;
    """).pl()
    print(roles)

    # Топ ядра сообщества (эксперты, которые чаще всего отвечают)
    print("\nТоп-10 участников «Ядра сообщества» (наибольшее число ответов и помощи):")
    core_users = con.execute("""
    SELECT
        display_name,
        user_id,
        count(*) as total_msgs,
        count(CASE WHEN reply_to_message_id IS NOT NULL THEN 1 END) as replies_given,
        round(count(CASE WHEN reply_to_message_id IS NOT NULL THEN 1 END) * 100.0 / count(*), 1) as reply_pct,
        sum(reactions_count) as total_reactions_received
    FROM fact_messages
    WHERE user_id LIKE 'user%' AND display_name NOT LIKE '%бот%' AND display_name NOT LIKE '%bot%'
    GROUP BY display_name, user_id
    HAVING total_msgs > 50
    ORDER BY replies_given DESC
    LIMIT 10;
    """).pl()
    print(core_users)

    # 4. ЗАДАЧА 3: ДОСТУПНОСТЬ АУДИТОРИИ
    print("\n" + "=" * 60)
    print("ЗАДАЧА 3: ДОСТУПНОСТЬ АУДИТОРИИ (USER ID, USERNAMES, КАНАЛЫ)")
    print("=" * 60)

    id_coverage = con.execute("""
    SELECT
        count(*) as total_messages,
        count(CASE WHEN user_id LIKE 'user%' THEN 1 END) as real_user_msgs,
        count(CASE WHEN user_id LIKE 'channel%' THEN 1 END) as channel_msgs,
        count(CASE WHEN user_id IS NULL THEN 1 END) as anonymous_msgs,
        round(count(CASE WHEN user_id LIKE 'user%' THEN 1 END) * 100.0 / count(*), 2) as user_coverage_pct
    FROM fact_messages;
    """).pl()
    print(id_coverage)

    # 5. ЗАДАЧА 4: ДИНАМИКА ИНТЕРЕСОВ ЗА 2026 ГОД И КЕЙС 15.09.2026
    print("\n" + "=" * 60)
    print("ЗАДАЧА 4: ДИНАМИКА ЗА 2026 ГОД И АНАЛИЗ ВСКОЛЫХАНИЯ ВОКРУГ 15.09.2026")
    print("=" * 60)

    monthly_2026 = con.execute("""
    SELECT
        strftime(date, '%Y-%m') as month,
        count(*) as messages,
        count(DISTINCT user_id) as active_users,
        sum(reactions_count) as reactions
    FROM fact_messages
    WHERE date >= '2026-01-01'
    GROUP BY month
    ORDER BY month;
    """).pl()
    print("\nЕжемесячная динамика за 2026 год:")
    print(monthly_2026)

    # Всплеск вокруг 15.09.2026 (визы и въезд)
    print("\nДинамика обсуждений виз/въезда в сентябре 2026:")
    sept_spike = con.execute("""
    SELECT
        strftime(date, '%Y-%m-%d') as day,
        count(*) as total_msgs,
        count(CASE WHEN regexp_matches(lower(text), '(виз|dtv|дтв|въезд|30 дней|60 дней|штамп|бордер|визаран)') THEN 1 END) as visa_msgs,
        sum(reactions_count) as total_reactions
    FROM fact_messages
    WHERE date BETWEEN '2026-09-08' AND '2026-09-22'
    GROUP BY day
    ORDER BY day;
    """).pl()
    print(sept_spike)

    # Топ сообщений с наибольшим числом реакций в сентябре 2026
    print("\nТоп сообщений с максимальным откликом (реакциями) в период всплеска 15.09.2026:")
    top_reactions = con.execute("""
    SELECT
        strftime(date, '%Y-%m-%d %H:%M') as datetime,
        display_name,
        reactions_count,
        substr(replace(text, chr(10), ' '), 1, 100) as preview
    FROM fact_messages
    WHERE date BETWEEN '2026-09-14' AND '2026-09-18'
    ORDER BY reactions_count DESC
    LIMIT 5;
    """).pl()
    print(top_reactions)

    # 6. РЕКОМЕНДАЦИИ ПО ВЫБОРУ 6 ЧАТОВ И ВЫГРУЗКА @VISADTV
    print("\n" + "=" * 60)
    print("ОТБОР 6 ЧАТОВ ДЛЯ РАСШИРЕНИЯ И СТАТУС @VISADTV")
    print("=" * 60)

    # Поиск упоминаний внешних каналов и чатов
    print("Упоминания группы @visadtv:")
    visadtv_count = con.execute("""
    SELECT
        count(*) as mentions_count,
        count(DISTINCT user_id) as independent_recommenders,
        count(DISTINCT chat_id) as chats_recommended_in
    FROM fact_messages
    WHERE lower(text) LIKE '%visadtv%' OR lower(text) LIKE '%t.me/visadtv%';
    """).pl()
    print(visadtv_count)

    con.close()

if __name__ == "__main__":
    run_analytics()
