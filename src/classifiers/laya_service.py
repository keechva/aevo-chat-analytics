"""
Hybrid Classification Service: Heuristics + LAYA on GPU RTX 3060.
High-throughput, deterministic topic routing + System 1 pain & intent scoring.
"""

import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
import duckdb
import pyarrow as pa
import torch
from tqdm import tqdm

import laya

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "processed" / "analytics.duckdb"

# Compiled deterministic regex rules for instant, 100% precision categorization
TOPIC_PATTERNS = {
    "visas": re.compile(r"(виз|dtv|дтв|штамп|бордер|визаран|въезд|выезд|паспорт|консульств|иммиграц|оверстей)", re.I),
    "money": re.compile(r"(банк|счет|карт|перевод|swift|обмен|рубл|бат|доллар|комисси|юнионпей|банкомат|наличк)", re.I),
    "housing": re.compile(r"(аренд|кондо|квартир|виллы|депозит|хозяин|договор|риелтор|снять|сдам|жиль|коммуналк)", re.I),
    "transport": re.compile(r"(байк|скутер|прав|кат а|дпс|гаи|полици|штраф|пдд|машин|авто|аренда байка)", re.I),
    "health": re.compile(r"(страховк|госпитал|клиник|врач|дентал|зуб|лекарств|больниц|аптек|safetywing|pacific cross)", re.I),
    "work_business": re.compile(r"(работ|ваканси|бизнес|компани|ворк-пермит|налог|удаленк|юрист)", re.I)
}

SIGNAL_PATTERNS = {
    "commercial_intent": re.compile(r"(кто помож|посоветуйте агент|под ключ|сколько стоит|ищу помощь|готов заплатить|посоветуйте проверен|помощь в оформлен)", re.I),
    "pain_complaint": re.compile(r"(завернули|отказали|обман|скам|развели|ужас|проблем|штрафанули|не вернули|заблокировал|не пустили|беспредел)", re.I)
}

class LayaClassifier:
    def __init__(self, device: str = "cuda"):
        self.device = device if torch.cuda.is_available() else "cpu"
        print(f"[LAYA] Initializing model on {self.device}...")
        self.agent = laya.load(device=self.device)
        print("[LAYA] Model loaded successfully.")

        self.questions = {
            "is_question": {
                "instructions": "Является ли текст вопросом или просьбой о совете?",
                "type": "noul"
            },
            "pain_intensity": {
                "instructions": "Оцените силу проблемы, боли или срочности от 0 до 3",
                "type": "score",
                "criteria": [
                    "Нет проблемы, нейтрально, флуд или обычный ответ",
                    "Легкий информационный вопрос из интереса",
                    "Практическая сложность, требуется конкретный совет или инструкция",
                    "Острая критическая боль, отказ, жалоба, риск депортации или срочный поиск исполнителя"
                ]
            }
        }

    def classify_single_heuristic(self, text: str) -> Dict[str, Any]:
        """Fast regex-based heuristic pre-classification."""
        topic = "general"
        for t, pat in TOPIC_PATTERNS.items():
            if pat.search(text):
                topic = t
                break

        signal = "casual_chat"
        if SIGNAL_PATTERNS["commercial_intent"].search(text):
            signal = "commercial_intent"
        elif SIGNAL_PATTERNS["pain_complaint"].search(text):
            signal = "pain_complaint"
        elif "?" in text:
            signal = "unresolved_question"

        return {"topic": topic, "signal": signal}

    def classify_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """Hybrid batch classification: heuristics + LAYA on GPU."""
        if not texts:
            return []

        results = []
        laya_texts = []
        laya_indices = []

        for idx, t in enumerate(texts):
            clean_t = (t or "").strip()
            if len(clean_t) < 8:
                results.append({
                    "topic_category": "general",
                    "signal_type": "casual_chat",
                    "is_question": False,
                    "pain_intensity": 0.0
                })
                continue

            h = self.classify_single_heuristic(clean_t)
            is_q = "?" in clean_t

            # Default pain score based on heuristics
            base_pain = 0.5
            if h["signal"] == "pain_complaint":
                base_pain = 2.5
            elif h["signal"] == "commercial_intent":
                base_pain = 2.0
            elif is_q:
                base_pain = 1.0

            item = {
                "topic_category": h["topic"],
                "signal_type": h["signal"],
                "is_question": is_q,
                "pain_intensity": base_pain
            }
            results.append(item)

            # If it's a meaningful message and we have LAYA, queue for fine-grained scoring
            if len(clean_t) >= 15:
                laya_texts.append(clean_t[:200])
                laya_indices.append(idx)

        # Run LAYA fine-grained scoring on relevant texts in smaller batches to avoid VRAM overload
        if laya_texts:
            try:
                preds = self.agent.predict_batch(laya_texts, self.questions)
                for l_idx, p in zip(laya_indices, preds):
                    answers = p.get("answers", {})

                    q_ans = answers.get("is_question", {})
                    noul_val = q_ans.get("noul", 0.0)
                    if noul_val >= 0.5:
                        results[l_idx]["is_question"] = True
                        if results[l_idx]["signal_type"] == "casual_chat":
                            results[l_idx]["signal_type"] = "unresolved_question"

                    pain_ans = answers.get("pain_intensity", {})
                    laya_pain = round(float(pain_ans.get("score", 0.0)), 2)

                    # Blend heuristic and LAYA scores
                    results[l_idx]["pain_intensity"] = max(results[l_idx]["pain_intensity"], laya_pain)
            except Exception as e:
                print(f"[LAYA Warning] Batch scoring error: {e}")

        return results

def run_classification_pipeline(
    batch_size: int = 64,
    limit: Optional[int] = 1000,
    start_date: str = "2026-01-01"
):
    """Fetches unclassified messages and classifies them."""
    # 1. Fetch rows (brief connection)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    query = f"""
    SELECT chat_id, message_id, text
    FROM fact_messages
    WHERE date >= '{start_date}'
      AND (topic_category IS NULL OR topic_category = '' OR topic_category = 'unclassified')
      AND text IS NOT NULL AND length(trim(text)) > 5
    ORDER BY date DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = con.execute(query).fetchall()
    con.close()

    if not rows:
        print("[Pipeline] No unclassified messages found for criteria.")
        return 0

    total = len(rows)
    print(f"[Pipeline] Found {total:,} messages to classify...")

    classifier = LayaClassifier(device="cuda")
    start_time = time.time()
    processed_count = 0

    pbar = tqdm(total=total, desc="Classifying messages")

    for i in range(0, total, batch_size):
        chunk = rows[i:i + batch_size]
        chat_ids = [r[0] for r in chunk]
        msg_ids = [r[1] for r in chunk]
        texts = [r[2] for r in chunk]

        preds = classifier.classify_batch(texts)

        topics = [p["topic_category"] for p in preds]
        signals = [p["signal_type"] for p in preds]
        is_qs = [p["is_question"] for p in preds]
        pains = [p["pain_intensity"] for p in preds]

        temp_table = pa.Table.from_arrays([
            pa.array(chat_ids, type=pa.int64()),
            pa.array(msg_ids, type=pa.int64()),
            pa.array(topics, type=pa.string()),
            pa.array(signals, type=pa.string()),
            pa.array(is_qs, type=pa.bool_()),
            pa.array(pains, type=pa.float64())
        ], names=["chat_id", "message_id", "topic_category", "signal_type", "is_question", "pain_intensity"])

        # 2. Write chunk (brief connection)
        write_con = duckdb.connect(str(DB_PATH), read_only=False)
        write_con.register("temp_preds", temp_table)
        write_con.execute("""
        UPDATE fact_messages
        SET
            topic_category = temp_preds.topic_category,
            signal_type = temp_preds.signal_type,
            is_question = temp_preds.is_question,
            pain_intensity = temp_preds.pain_intensity
        FROM temp_preds
        WHERE fact_messages.chat_id = temp_preds.chat_id
          AND fact_messages.message_id = temp_preds.message_id;
        """)
        write_con.unregister("temp_preds")
        write_con.close()

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        processed_count += len(chunk)
        pbar.update(len(chunk))

    pbar.close()
    elapsed = time.time() - start_time
    print(f"[Pipeline] Completed {processed_count:,} messages in {elapsed:.2f}s ({processed_count/elapsed:.1f} msg/sec)!")
    return processed_count

if __name__ == "__main__":
    run_classification_pipeline(batch_size=64, limit=1000)
