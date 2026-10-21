"""
Automated Problem Clustering & Opportunity Scoring Engine.
Synthesizes thousands of raw messages into structured pain clusters with real quotes
and calculates Opportunity Scores for product founders.
"""

import json
import math
from pathlib import Path
from typing import List, Dict, Any
import duckdb

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "processed" / "analytics.duckdb"

CLUSTERS_CONFIG = [
    {
        "cluster_id": "th_visas_dtv",
        "country_code": "TH",
        "topic": "visas",
        "title": "Отказы и ужесточение требований по DTV-визе",
        "description": "С 31 августа и 15 сентября консульства ввели разночтения по справкам о несудимости, подтверждению ПМЖ и выпискам из банков. Экспаты ищут надежных посредников.",
        "sql_cond": "(lower(m.text) LIKE '%dtv%' OR lower(m.text) LIKE '%дтв%')",
        "keywords": ["dtv", "дтв", "destination thailand", "visadtv"]
    },
    {
        "cluster_id": "th_money_airbnb_booking",
        "country_code": "TH",
        "topic": "money",
        "title": "Блокировка оплаты аренды жилья (AirBnb, Booking) из РФ",
        "description": "Невозможность оплатить отели и долгосрочную аренду российскими картами, задержки подтверждения броней, необходимость зарубежных карт.",
        "sql_cond": "((lower(m.text) LIKE '%airbnb%' OR lower(m.text) LIKE '%booking%' OR lower(m.text) LIKE '%букинг%') AND (lower(m.text) LIKE '%оплат%' OR lower(m.text) LIKE '%карт%' OR lower(m.text) LIKE '%рф%' OR lower(m.text) LIKE '%мир%' OR lower(m.text) LIKE '%unionpay%'))",
        "keywords": ["airbnb", "booking", "оплата airbnb", "карты рф"]
    },
    {
        "cluster_id": "th_money_bank_accounts",
        "country_code": "TH",
        "topic": "money",
        "title": "Открытие счетов в тайских банках и Resident Certificate",
        "description": "Сложности с открытием счета в Bangkok Bank и Kasikorn: требуют контракт аренды от года, сертификат резидента, покупку дорогой страховки.",
        "sql_cond": "((lower(m.text) LIKE '%банк%' OR lower(m.text) LIKE '%bangkok bank%' OR lower(m.text) LIKE '%kasikorn%') AND (lower(m.text) LIKE '%открыт%' OR lower(m.text) LIKE '%счет%' OR lower(m.text) LIKE '%resident certificate%' OR lower(m.text) LIKE '%резидентск%'))",
        "keywords": ["bangkok bank", "kasikorn", "открыть счет", "resident certificate"]
    },
    {
        "cluster_id": "th_health_insurance_denials",
        "country_code": "TH",
        "topic": "health",
        "title": "Отказы страховых компаний (Genki, SafetyWing) в выплатах",
        "description": "Страховые отказывают при нахождении в Таиланде более 6 недель или заявляют Таиланд как 'домашнюю страну'. Огромные счета в тайских госпиталях.",
        "sql_cond": "((lower(m.text) LIKE '%страхов%' OR lower(m.text) LIKE '%госпитал%') AND (lower(m.text) LIKE '%отказ%' OR lower(m.text) LIKE '%не покрыв%' OR lower(m.text) LIKE '%выплат%' OR lower(m.text) LIKE '%франшиз%'))",
        "keywords": ["страховка", "госпиталь", "safetywing", "genki", "отказ в выплате"]
    },
    {
        "cluster_id": "th_transport_bike_police",
        "country_code": "TH",
        "topic": "transport",
        "title": "Облавы полиции, тайские права категории А и штрафы за байки",
        "description": "Регулярные рейды дорожной полиции на Пхукете и в Паттайе. Отсутствие категории А в правах аннулирует медицинскую страховку при ДТП.",
        "sql_cond": "((lower(m.text) LIKE '%байк%' OR lower(m.text) LIKE '%скутер%') AND (lower(m.text) LIKE '%права%' OR lower(m.text) LIKE '%облав%' OR lower(m.text) LIKE '%полици%' OR lower(m.text) LIKE '%штраф%' OR lower(m.text) LIKE '%мву%'))",
        "keywords": ["права категория а", "полиция", "облавы", "штраф байк"]
    },
    {
        "cluster_id": "th_housing_deposit_disputes",
        "country_code": "TH",
        "topic": "housing",
        "title": "Невозврат депозитов лендлордами при выселении из кондо",
        "description": "Хозяева кондоминиумов удерживают 1-2 месячных депозита под предлогом уборки или скрытых повреждений. Отсутствие юридической защиты арендаторов.",
        "sql_cond": "((lower(m.text) LIKE '%депозит%' OR lower(m.text) LIKE '%залог%') AND (lower(m.text) LIKE '%не вернул%' OR lower(m.text) LIKE '%лендлорд%' OR lower(m.text) LIKE '%кондо%' OR lower(m.text) LIKE '%удержал%'))",
        "keywords": ["депозит кондо", "залог", "лендлорд не вернул", "выселение"]
    }
]

def update_problem_clusters():
    con = duckdb.connect(str(DB_PATH), read_only=False)
    print("Updating problem clusters and opportunity scores...")

    for c in CLUSTERS_CONFIG:
        cond = c["sql_cond"]

        query = f"""
        SELECT 
            count(*) as mentions_count,
            round(avg(COALESCE(m.pain_intensity, 1.8)), 2) as avg_pain,
            round(count(CASE WHEN m.signal_type = 'commercial_intent' OR lower(m.text) LIKE '%агент%' OR lower(m.text) LIKE '%помощ%' OR lower(m.text) LIKE '%оплат%' THEN 1 END) * 100.0 / GREATEST(count(*), 1), 1) as commercial_intent_pct
        FROM fact_messages m
        WHERE {cond};
        """
        stat = con.execute(query).fetchone()
        mentions = stat[0] or 0
        avg_pain = max(1.5, float(stat[1] or 2.0))
        commercial_pct = float(stat[2] or 0.0)

        # Opportunity Score formula:
        # 1. Volume score (up to 50 pts, log scale benchmarked to 2000 msgs)
        vol_score = (math.log10(max(mentions, 1)) / math.log10(2000.0)) * 50.0
        # 2. Pain intensity score (up to 30 pts)
        pain_score = (avg_pain / 3.0) * 30.0
        # 3. Commercial intent score (up to 20 pts)
        comm_score = min(20.0, commercial_pct * 2.0)
        opp_score = round(min(98.5, max(20.0, vol_score + pain_score + comm_score)), 1)

        # Extract top 3 most authentic, painful user questions/quotes
        quotes_query = f"""
        SELECT 
            m.message_id,
            m.display_name,
            strftime(m.date, '%Y-%m-%d') as date_str,
            substr(replace(m.text, chr(10), ' '), 1, 190) as quote,
            COALESCE(m.pain_intensity, 2.5) as pain,
            COALESCE(m.reactions_count, 0) as reactions
        FROM fact_messages m
        WHERE {cond}
          AND (m.text LIKE '%?%' OR lower(m.text) LIKE '%не могу%' OR lower(m.text) LIKE '%подскажите%' OR lower(m.text) LIKE '%проблем%' OR lower(m.text) LIKE '%отказ%')
          AND lower(m.text) NOT LIKE '%t.me/%'
          AND lower(m.text) NOT LIKE '%http%'
          AND lower(COALESCE(m.display_name, '')) NOT LIKE '%bot%'
          AND length(trim(m.text)) BETWEEN 40 AND 280
        ORDER BY m.date DESC
        LIMIT 3;
        """
        quotes_rows = con.execute(quotes_query).fetchall()
        quotes = []
        for q in quotes_rows:
            quotes.append({
                "message_id": q[0],
                "author": q[1] or "Участник",
                "date": q[2],
                "text": q[3] + "...",
                "pain": q[4],
                "reactions": q[5]
            })

        con.execute("""
        INSERT OR REPLACE INTO dim_problem_clusters (
            cluster_id, country_code, topic, title, description,
            keywords, opportunity_score, mentions_count,
            avg_pain, commercial_intent_pct, sample_quotes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, [
            c["cluster_id"],
            c["country_code"],
            c["topic"],
            c["title"],
            c["description"],
            json.dumps(c["keywords"], ensure_ascii=False),
            opp_score,
            mentions,
            avg_pain,
            commercial_pct,
            json.dumps(quotes, ensure_ascii=False)
        ])

        print(f"[{c['country_code']}] {c['title']} -> Mentions: {mentions}, Opp Score: {opp_score}/100, Quotes: {len(quotes)}")

    con.close()
    print("Problem clusters and opportunity scores updated successfully!")

if __name__ == "__main__":
    update_problem_clusters()
