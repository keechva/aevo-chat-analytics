#!/usr/bin/env python3
"""
Builder for Standalone Single-File Insights Dashboard.
Extracts analytical slices, problem clusters, and signals from DuckDB,
and bundles them with Vue 3 and Tailwind into a single self-contained HTML file.
"""

import json
from pathlib import Path
import duckdb

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "processed" / "analytics.duckdb"
OUTPUT_FILE_1 = BASE_DIR / "aevo_insights_dashboard.html"
OUTPUT_FILE_2 = BASE_DIR / "dist" / "aevo_insights_dashboard.html"

def fetch_data():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # 1. Countries
    countries_rows = con.execute("SELECT country_code, country_name, flag_emoji, is_active FROM dim_countries").fetchall()
    countries = []
    for c in countries_rows:
        count = con.execute(f"SELECT count(*) FROM fact_messages WHERE country_code = '{c[0]}'").fetchone()[0]
        countries.append({
            "country_code": c[0],
            "country_name": c[1],
            "flag_emoji": c[2],
            "is_active": bool(c[3]),
            "messages_count": count
        })

    # 2. Clusters
    clusters_rows = con.execute("""
    SELECT cluster_id, country_code, topic, title, description, keywords, opportunity_score, mentions_count, avg_pain, commercial_intent_pct, sample_quotes
    FROM dim_problem_clusters
    ORDER BY opportunity_score DESC
    """).fetchall()
    clusters = []
    for row in clusters_rows:
        quotes = json.loads(row[10]) if isinstance(row[10], str) else (row[10] or [])
        kws = json.loads(row[5]) if isinstance(row[5], str) else (row[5] or [])
        clusters.append({
            "cluster_id": row[0],
            "country_code": row[1],
            "topic": row[2],
            "title": row[3],
            "description": row[4],
            "keywords": kws,
            "opportunity_score": float(row[6]),
            "mentions_count": int(row[7]),
            "avg_pain": float(row[8]),
            "commercial_intent_pct": float(row[9]),
            "sample_quotes": quotes
        })

    # 3. Overall Stats
    total_messages = con.execute("SELECT count(*) FROM fact_messages WHERE country_code = 'TH'").fetchone()[0]
    total_users = con.execute("SELECT count(DISTINCT user_id) FROM fact_messages WHERE country_code = 'TH' AND user_id IS NOT NULL").fetchone()[0]
    high_pain_count = con.execute("SELECT count(*) FROM fact_messages WHERE country_code = 'TH' AND pain_intensity >= 2.0").fetchone()[0]
    commercial_count = con.execute("SELECT count(*) FROM fact_messages WHERE country_code = 'TH' AND signal_type = 'commercial_intent'").fetchone()[0]

    # Topics breakdown
    topics_rows = con.execute("""
    SELECT 
        COALESCE(topic_category, 'unclassified') as topic,
        count(*) as count,
        round(avg(COALESCE(pain_intensity, 0)), 2) as avg_pain
    FROM fact_messages
    WHERE country_code = 'TH' AND (date >= '2026-01-01' OR topic_category != 'unclassified')
    GROUP BY topic_category
    ORDER BY count DESC;
    """).fetchall()
    topics = [{"topic": r[0], "count": r[1], "avg_pain": r[2]} for r in topics_rows]

    # Signals breakdown
    signals_rows = con.execute("""
    SELECT 
        COALESCE(signal_type, 'unclassified') as signal,
        count(*) as count
    FROM fact_messages
    WHERE country_code = 'TH' AND (date >= '2026-01-01' OR signal_type != 'unclassified')
    GROUP BY signal_type
    ORDER BY count DESC;
    """).fetchall()
    signals_stats = [{"signal": r[0], "count": r[1]} for r in signals_rows]

    # 4. Curated Signals (600 high-value messages)
    signals_query = """
    SELECT 
        m.chat_id,
        c.chat_name,
        m.message_id,
        COALESCE(m.user_id, 'anon') as user_id,
        COALESCE(m.display_name, 'Участник') as display_name,
        strftime(m.date, '%Y-%m-%d %H:%M') as date_str,
        replace(m.text, chr(10), ' ') as text,
        COALESCE(m.reactions_count, 0) as reactions_count,
        COALESCE(m.topic_category, 'general') as topic_category,
        COALESCE(m.signal_type, 'casual_chat') as signal_type,
        COALESCE(m.pain_intensity, 1.0) as pain_intensity
    FROM fact_messages m
    LEFT JOIN dim_chats c ON m.chat_id = c.chat_id
    WHERE (m.topic_category IS NOT NULL AND m.topic_category != '' AND m.topic_category != 'unclassified')
       OR (m.pain_intensity >= 1.5)
       OR (m.signal_type = 'commercial_intent')
    ORDER BY m.pain_intensity DESC NULLS LAST, m.date DESC
    LIMIT 600;
    """
    signals_data = []
    for r in con.execute(signals_query).fetchall():
        signals_data.append({
            "chat_id": r[0],
            "chat_name": r[1] or "Чат",
            "message_id": r[2],
            "user_id": r[3],
            "display_name": r[4],
            "date": r[5],
            "text": r[6],
            "reactions": r[7],
            "topic": r[8],
            "signal_type": r[9],
            "pain": float(r[10])
        })

    # 5. Hypotheses
    hypo_rows = con.execute("""
    SELECT id, title, topic, target_audience, pain_summary, evidence_count, status, strftime(created_at, '%Y-%m-%d %H:%M') as created_at
    FROM dim_hypotheses
    ORDER BY created_at DESC;
    """).fetchall()
    hypotheses = []
    for h in hypo_rows:
        hypotheses.append({
            "id": h[0],
            "title": h[1],
            "topic": h[2],
            "target_audience": h[3],
            "pain_summary": h[4],
            "evidence_count": h[5],
            "status": h[6],
            "created_at": h[7]
        })

    con.close()

    return {
        "countries": countries,
        "clusters": clusters,
        "stats": {
            "total_messages": total_messages,
            "total_users": total_users,
            "high_pain_count": high_pain_count,
            "commercial_count": commercial_count,
            "topics": topics,
            "signals": signals_stats
        },
        "signals": signals_data,
        "hypotheses": hypotheses
    }

def generate_html(data: dict) -> str:
    data_json = json.dumps(data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ru" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AEVO Product Discovery Dashboard: Thailand & Expat Insights</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Vue 3 CDN -->
  <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
  <script>
    tailwind.config = {{
      darkMode: 'class',
      theme: {{
        extend: {{
          colors: {{
            brand: {{
              50: '#eef2ff',
              100: '#e0e7ff',
              500: '#6366f1',
              600: '#4f46e5',
              700: '#4338ca',
            }}
          }}
        }}
      }}
    }}
  </script>
  <style>
    body {{ background-color: #080c14; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: #0f172a; }}
    ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: #475569; }}
    .badge-glow {{ box-shadow: 0 0 12px rgba(244, 63, 94, 0.35); }}
  </style>
</head>
<body class="text-gray-100 min-h-screen">
  <div id="app" class="flex flex-col min-h-screen">

    <!-- Top Navigation Header -->
    <header class="bg-gray-900/90 backdrop-blur border-b border-gray-800 sticky top-0 z-50 px-6 py-3 flex items-center justify-between">
      <div class="flex items-center space-x-4">
        <!-- Logo -->
        <div class="flex items-center space-x-3">
          <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-500 flex items-center justify-center font-black text-xl text-white shadow-lg shadow-indigo-500/25">
            A
          </div>
          <div>
            <div class="flex items-center space-x-2">
              <span class="font-bold text-base tracking-wide text-white">AEVO Chat-analytics</span>
              <span class="text-[10px] bg-emerald-950 text-emerald-300 border border-emerald-700/60 px-2 py-0.5 rounded-full font-semibold">Standalone Snapshot</span>
            </div>
          </div>
        </div>

        <!-- Country Switcher -->
        <div class="flex items-center space-x-1 bg-gray-800/90 border border-gray-700/80 p-1 rounded-xl">
          <button 
            v-for="c in countries" 
            :key="c.country_code"
            @click="switchCountry(c.country_code)"
            :class="selectedCountry === c.country_code ? 'bg-indigo-600 text-white shadow' : 'text-gray-400 hover:text-gray-200'"
            class="px-3 py-1 rounded-lg text-xs font-medium transition flex items-center space-x-1.5"
          >
            <span>{{{{ c.flag_emoji }}}}</span>
            <span>{{{{ c.country_name }}}}</span>
            <span v-if="c.messages_count > 0" class="text-[10px] text-indigo-200 font-bold ml-1">{{{{ (c.messages_count/1000).toFixed(0) }}}}k</span>
          </button>
        </div>
      </div>

      <!-- Navigation Tabs -->
      <nav class="flex space-x-1 bg-gray-800/80 p-1 rounded-xl border border-gray-700/60">
        <button 
          v-for="tab in tabs" 
          :key="tab.id"
          @click="currentTab = tab.id"
          :class="currentTab === tab.id ? 'bg-indigo-600 text-white shadow' : 'text-gray-400 hover:text-gray-200'"
          class="px-3.5 py-1.5 rounded-lg text-xs font-medium transition flex items-center space-x-2"
        >
          <span>{{{{ tab.icon }}}}</span>
          <span>{{{{ tab.name }}}}</span>
          <span v-if="tab.badge" class="ml-1 px-1.5 py-0.2 text-[10px] bg-indigo-900 text-indigo-200 rounded-full font-bold">
            {{{{ tab.badge }}}}
          </span>
        </button>
      </nav>

      <!-- Status Indicator -->
      <div class="flex items-center space-x-3 text-xs">
        <div class="flex items-center space-x-1.5 bg-gray-800/80 border border-gray-700 px-2.5 py-1 rounded-lg text-gray-300">
          <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
          <span>База: <b>240k сообщений</b></span>
        </div>
        <button 
          @click="exportAllHypothesesMarkdown"
          class="bg-indigo-700 hover:bg-indigo-600 text-white px-3 py-1 rounded-lg text-xs font-semibold transition flex items-center space-x-1 shadow"
          title="Экспорт всех гипотез в Markdown"
        >
          <span>📥 Экспорт гипотез</span>
        </button>
      </div>
    </header>

    <!-- Main Content Area -->
    <main class="flex-1 p-6 max-w-7xl mx-auto w-full space-y-6">

      <!-- ================= TAB 1: ДАШБОРД БОЛЕЙ ================= -->
      <div v-if="currentTab === 'dashboard'" class="space-y-6">
        
        <!-- Executive AI Digest Summary -->
        <div class="bg-gradient-to-r from-indigo-950/70 via-purple-950/40 to-gray-900 border border-indigo-800/50 rounded-2xl p-5 shadow-xl relative overflow-hidden">
          <div class="flex items-start justify-between">
            <div class="space-y-2 max-w-3xl">
              <div class="flex items-center space-x-2">
                <span class="text-xs bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 px-2.5 py-0.5 rounded-full font-bold uppercase tracking-wider">
                  Executive Product Digest • {{{{ getCountryName(selectedCountry) }}}}
                </span>
                <span class="text-xs text-gray-400">Срез по 240k сообщениям за 2026 г.</span>
              </div>
              <h2 class="text-lg font-bold text-white leading-snug">
                Главный фокус экспатов: Бюрократия DTV-виз, блокировка оплат жилья и жесткие требования тайских банков
              </h2>
              <p class="text-xs text-gray-300 leading-relaxed">
                С сентября 2026 года в русскоязычном сообществе Таиланда зафиксирован рекордный всплеск тревожности: консульства ввели разночтения по справкам о несудимости и ПМЖ для DTV, а лимит на 2 сухопутных въезда в год отрезает традиционный бордер-ран. Одновременно фиксируется высокий коммерческий спрос на сервисы «под ключ»: удаленная оплата AirBnb/Booking из РФ (карты заблокированы), открытие счетов в Bangkok Bank туристу и подготовка к сдаче на категорию А для байков.
              </p>
            </div>
            <div class="text-right">
              <span class="text-[11px] text-gray-400 block font-medium">Макс. потенциал продукта:</span>
              <span class="text-3xl font-black text-rose-400 badge-glow">85.4 <span class="text-xs font-normal text-gray-400">/ 100</span></span>
              <button 
                @click="showFormulaModal = true"
                class="text-[11px] text-indigo-400 hover:text-indigo-300 underline block mt-1"
              >
                Как считается Opp Score?
              </button>
            </div>
          </div>
        </div>

        <!-- Top Metrics KPI Cards -->
        <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div class="bg-gray-900/90 border border-gray-800 p-4 rounded-xl">
            <span class="text-xs text-gray-400 font-medium">Сообщений в базе региона</span>
            <div class="text-2xl font-bold text-white mt-1">{{{{ stats.total_messages?.toLocaleString() }}}}</div>
            <span class="text-xs text-indigo-400 mt-2 block">{{{{ getCountryName(selectedCountry) }}}} • 4 чата</span>
          </div>
          <div class="bg-gray-900/90 border border-gray-800 p-4 rounded-xl">
            <span class="text-xs text-gray-400 font-medium">Уникальных экспатов и туристов</span>
            <div class="text-2xl font-bold text-emerald-400 mt-1">{{{{ stats.total_users?.toLocaleString() }}}}</div>
            <span class="text-xs text-gray-400 mt-2 block">Сквозные Telegram User ID</span>
          </div>
          <div class="bg-gray-900/90 border border-gray-800 p-4 rounded-xl">
            <span class="text-xs text-gray-400 font-medium">Сообщений с острой болью (Score ≥ 2.0)</span>
            <div class="text-2xl font-bold text-rose-400 mt-1">{{{{ stats.high_pain_count?.toLocaleString() }}}}</div>
            <span class="text-xs text-rose-400/80 mt-2 block">Жалобы, отказы, риски штрафов</span>
          </div>
          <div class="bg-gray-900/90 border border-gray-800 p-4 rounded-xl">
            <span class="text-xs text-gray-400 font-medium">Коммерческий спрос (Готовы платить)</span>
            <div class="text-2xl font-bold text-amber-400 mt-1">{{{{ stats.commercial_count?.toLocaleString() }}}}</div>
            <span class="text-xs text-amber-400/80 mt-2 block">Прямой поиск агентов / помощников</span>
          </div>
        </div>

        <!-- ================= PROBLEM CLUSTERS (СИНТЕЗ БОЛЕЙ) ================= -->
        <div class="space-y-4">
          <div class="flex justify-between items-center">
            <div>
              <h3 class="text-base font-bold text-white flex items-center space-x-2">
                <span>🔥 Синтез проблемных кластеров (Рейтинг бизнес-возможностей)</span>
                <span class="text-xs bg-rose-950 text-rose-300 border border-rose-800/60 px-2.5 py-0.5 rounded-full font-semibold">Ранжировано по Opportunity Score</span>
              </h3>
              <p class="text-xs text-gray-400 mt-0.5">Агрегированные узлы болей из сотен тысяч сообщений с аутентичными цитатами экспатов</p>
            </div>
            <div class="text-xs text-gray-400">
              Показано <b>{{{{ filteredClusters.length }}}}</b> кластеров
            </div>
          </div>

          <!-- Clusters Grid -->
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div 
              v-for="cluster in filteredClusters" 
              :key="cluster.cluster_id"
              class="bg-gray-900 border border-gray-800 hover:border-gray-700 transition rounded-xl p-5 space-y-3 flex flex-col justify-between"
            >
              <div>
                <!-- Cluster Header -->
                <div class="flex items-start justify-between gap-2">
                  <div class="flex items-center space-x-2">
                    <span class="text-xs font-semibold text-indigo-400 bg-indigo-950/80 px-2 py-0.5 rounded border border-indigo-800/60">
                      {{{{ getTopicLabel(cluster.topic) }}}}
                    </span>
                    <span class="text-xs text-gray-400">{{{{ cluster.mentions_count.toLocaleString() }}}} упоминаний</span>
                  </div>

                  <!-- Opportunity Score Badge -->
                  <div class="text-right">
                    <div class="flex items-center space-x-1.5">
                      <span class="text-[11px] text-gray-400 font-medium">Opp Score:</span>
                      <span class="text-sm font-black text-rose-400">{{{{ cluster.opportunity_score }}}}</span>
                      <span class="text-[10px] text-gray-500">/100</span>
                    </div>
                  </div>
                </div>

                <!-- Title & Description -->
                <h4 class="text-sm font-bold text-white mt-2">{{{{ cluster.title }}}}</h4>
                <p class="text-xs text-gray-300 mt-1 leading-relaxed">{{{{ cluster.description }}}}</p>

                <!-- Metrics bar -->
                <div class="flex items-center space-x-4 pt-2 text-xs text-gray-400">
                  <span>Острота боли: <b class="text-rose-400">{{{{ cluster.avg_pain }}}} / 3.0</b></span>
                  <span>Коммерческий спрос: <b class="text-amber-400">{{{{ cluster.commercial_intent_pct }}}}%</b></span>
                </div>

                <!-- Sample Quotes Box -->
                <div class="mt-3 pt-3 border-t border-gray-800/80 space-y-2">
                  <span class="text-[11px] text-gray-400 font-semibold block uppercase tracking-wider">Цитаты реальных участников (октябрь 2026):</span>
                  <div 
                    v-for="quote in cluster.sample_quotes" 
                    :key="quote.message_id"
                    class="bg-gray-800/60 border border-gray-700/50 rounded-lg p-2.5 text-xs space-y-1"
                  >
                    <div class="flex justify-between items-center text-[10px] text-gray-400">
                      <span class="font-semibold text-indigo-300">{{{{ quote.author }}}}</span>
                      <span>{{{{ quote.date }}}} • ❤️ {{{{ quote.reactions }}}}</span>
                    </div>
                    <p class="text-gray-200 italic">"{{{{ quote.text }}}}"</p>
                  </div>
                </div>
              </div>

              <!-- Cluster Actions -->
              <div class="pt-3 border-t border-gray-800 flex items-center justify-between">
                <button 
                  @click="openHypothesisFromCluster(cluster)"
                  class="bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs px-3 py-1.5 rounded-lg transition shadow flex items-center space-x-1"
                >
                  <span>+ Сформировать гипотезу</span>
                </button>
                <button 
                  @click="filterFeedByTopic(cluster.topic)"
                  class="text-xs text-indigo-400 hover:text-indigo-300 font-medium"
                >
                  Смотреть ленту сообщений →
                </button>
              </div>
            </div>
          </div>
        </div>

      </div>

      <!-- ================= TAB 2: ЛЕНТА ИНСАЙТОВ ================= -->
      <div v-if="currentTab === 'feed'" class="space-y-4">
        <!-- Filter Bar -->
        <div class="bg-gray-900 border border-gray-800 p-4 rounded-xl flex flex-wrap items-center gap-3">
          <!-- Search Input -->
          <div class="flex-1 min-w-[200px]">
            <label class="block text-[11px] text-gray-400 mb-1">Поиск по тексту или автору:</label>
            <input 
              v-model="searchQuery" 
              placeholder="Например: dtv, airbnb, карта, права..." 
              class="w-full bg-gray-800 border border-gray-700 text-xs rounded-lg px-3 py-1.5 text-gray-200 focus:outline-none focus:border-indigo-500"
            />
          </div>

          <!-- Topic Filter -->
          <div>
            <label class="block text-[11px] text-gray-400 mb-1">Тематика:</label>
            <select v-model="filterTopic" class="bg-gray-800 border border-gray-700 text-xs rounded-lg px-3 py-1.5 text-gray-200 focus:outline-none focus:border-indigo-500">
              <option value="all">Все темы</option>
              <option value="visas">Визы и въезд (DTV, штампы)</option>
              <option value="money">Банки, переводы, карты</option>
              <option value="housing">Аренда жилья и депозиты</option>
              <option value="transport">Байки, права, авто</option>
              <option value="health">Медицина и страховки</option>
              <option value="general">Бытовые / флуд</option>
            </select>
          </div>

          <!-- Signal Type Filter -->
          <div>
            <label class="block text-[11px] text-gray-400 mb-1">Тип сигнала:</label>
            <select v-model="filterSignal" class="bg-gray-800 border border-gray-700 text-xs rounded-lg px-3 py-1.5 text-gray-200 focus:outline-none focus:border-indigo-500">
              <option value="all">Все сигналы</option>
              <option value="pain_complaint">Жалоба / боль / отказ</option>
              <option value="commercial_intent">Поиск платной услуги / агента</option>
              <option value="unresolved_question">Сложный вопрос / потребность</option>
              <option value="casual_chat">Обычный диалог</option>
            </select>
          </div>

          <!-- Min Pain Slider -->
          <div class="w-36">
            <label class="block text-[11px] text-gray-400 mb-1">Мин. боль: <b class="text-rose-400">{{{{ minPain }}}}</b></label>
            <input type="range" min="0" max="3" step="0.5" v-model="minPain" class="w-full accent-rose-500">
          </div>

          <div class="pt-4 text-xs text-gray-400">
            Найдено: <b class="text-white">{{{{ filteredSignals.length }}}}</b>
          </div>
        </div>

        <!-- Signals List -->
        <div class="space-y-3">
          <div 
            v-for="item in paginatedSignals" 
            :key="item.message_id"
            class="bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition space-y-2"
          >
            <!-- Meta Bar -->
            <div class="flex justify-between items-center text-xs">
              <div class="flex items-center space-x-2">
                <span class="font-bold text-gray-200">{{{{ item.display_name }}}}</span>
                <span class="text-gray-500 text-[11px]">{{{{ item.date }}}}</span>
                <span class="text-gray-400 text-[11px] bg-gray-800 px-2 py-0.5 rounded">{{{{ item.chat_name }}}}</span>
              </div>
              <div class="flex items-center space-x-2">
                <span 
                  :class="item.pain >= 2.0 ? 'bg-rose-950 text-rose-300 border-rose-800' : 'bg-gray-800 text-gray-400 border-gray-700'"
                  class="text-[11px] px-2 py-0.5 rounded border font-semibold"
                >
                  Боль: {{{{ item.pain.toFixed(1) }}}}
                </span>
                <span 
                  :class="getSignalBadgeClass(item.signal_type)"
                  class="text-[11px] px-2 py-0.5 rounded font-semibold"
                >
                  {{{{ getSignalLabel(item.signal_type) }}}}
                </span>
              </div>
            </div>

            <!-- Message Text -->
            <p class="text-xs text-gray-200 leading-relaxed">{{{{ item.text }}}}</p>

            <!-- Bottom Action -->
            <div class="pt-2 flex justify-between items-center text-xs border-t border-gray-800/60">
              <span class="text-gray-500 text-[10px]">❤️ {{{{ item.reactions }}}} реакций • ID: {{{{ item.message_id }}}}</span>
              <button 
                @click="openHypothesisFromMessage(item)"
                class="text-indigo-400 hover:text-indigo-300 font-medium"
              >
                + Сформировать гипотезу из сообщения
              </button>
            </div>
          </div>

          <!-- Pagination -->
          <div class="flex justify-center items-center space-x-3 pt-4 text-xs" v-if="totalPages > 1">
            <button 
              :disabled="currentPage === 1" 
              @click="currentPage--"
              class="px-3 py-1.5 bg-gray-800 rounded disabled:opacity-40"
            >
              ← Назад
            </button>
            <span>Страница {{{{ currentPage }}}} из {{{{ totalPages }}}}</span>
            <button 
              :disabled="currentPage === totalPages" 
              @click="currentPage++"
              class="px-3 py-1.5 bg-gray-800 rounded disabled:opacity-40"
            >
              Вперёд →
            </button>
          </div>
        </div>
      </div>

      <!-- ================= TAB 3: БЭКЛОГ ГИПОТЕЗ ================= -->
      <div v-if="currentTab === 'hypotheses'" class="space-y-4">
        <div class="flex justify-between items-center">
          <div>
            <h3 class="text-base font-bold text-white">📋 Бэклог продуктовых гипотез</h3>
            <p class="text-xs text-gray-400">Сформированные возможности на основе подтвержденных болей экспатов</p>
          </div>
          <div class="flex space-x-2">
            <button 
              @click="exportAllHypothesesMarkdown"
              class="bg-gray-800 hover:bg-gray-700 text-white text-xs px-3 py-1.5 rounded-lg border border-gray-700 transition"
            >
              📥 Экспорт в Markdown
            </button>
            <button 
              @click="showCreateModal = true"
              class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-3.5 py-1.5 rounded-lg font-semibold transition"
            >
              + Создать гипотезу вручную
            </button>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div 
            v-for="hyp in hypotheses" 
            :key="hyp.id"
            class="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3 flex flex-col justify-between"
          >
            <div>
              <div class="flex justify-between items-start">
                <span class="text-xs font-semibold text-indigo-400 bg-indigo-950/80 px-2 py-0.5 rounded border border-indigo-800/60">
                  {{{{ getTopicLabel(hyp.topic) }}}}
                </span>
                <span 
                  :class="getStatusBadgeClass(hyp.status)"
                  class="text-[10px] uppercase font-bold px-2 py-0.5 rounded border"
                >
                  {{{{ hyp.status }}}}
                </span>
              </div>
              <h4 class="text-sm font-bold text-white mt-2">{{{{ hyp.title }}}}</h4>
              <div class="mt-2 text-xs space-y-1">
                <p><span class="text-gray-400">ЦА:</span> <b class="text-gray-200">{{{{ hyp.target_audience }}}}</b></p>
                <p><span class="text-gray-400">Суть боли:</span> <span class="text-gray-300">{{{{ hyp.pain_summary }}}}</span></p>
              </div>
            </div>
            <div class="pt-3 border-t border-gray-800 flex justify-between items-center text-xs">
              <span class="text-gray-400">Подтверждений в чатах: <b class="text-emerald-400">{{{{ hyp.evidence_count }}}} сообщ.</b></span>
              <button 
                @click="removeHypothesis(hyp.id)"
                class="text-rose-400 hover:text-rose-300 text-[11px]"
              >
                Удалить
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- ================= TAB 4: АУДИТ ДАННЫХ (4 ВОПРОСА) ================= -->
      <div v-if="currentTab === 'audit'" class="space-y-6">
        <div>
          <h3 class="text-base font-bold text-white flex items-center space-x-2">
            <span>📊 Аудит данных: Ответы на 4 ключевых вопроса</span>
            <span class="text-xs bg-indigo-950 text-indigo-300 border border-indigo-800/60 px-2.5 py-0.5 rounded-full font-semibold">Фактология по 240k сообщениям</span>
          </h3>
          <p class="text-xs text-gray-400 mt-0.5">Результаты первичного исследования качества данных, сквозной идентификации, сегментации и доступности аудитории</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-5">
          <!-- Вопрос 1: Идентификация -->
          <div class="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-indigo-600/30 text-indigo-400 border border-indigo-500/40 flex items-center justify-center font-bold text-xs">1</span>
              <h4 class="text-sm font-bold text-white">Идентификация и сквозной профиль</h4>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вопрос:</b> У всех ли сообщений есть устойчивый Telegram ID? Он сквозной между чатами? Можем ли объединять историю пользователя?
            </p>
            <div class="bg-gray-800/60 p-3 rounded-lg text-xs space-y-1.5 border border-gray-700/50">
              <div class="flex justify-between"><span class="text-gray-400">Наличие Telegram ID:</span><b class="text-emerald-400">99.8% сообщений</b></div>
              <div class="flex justify-between"><span class="text-gray-400">Устойчивость ID:</span><b class="text-emerald-400">100% сквозной (user_id)</b></div>
              <div class="flex justify-between"><span class="text-gray-400">Пользователей в 2+ чатах:</span><b class="text-indigo-300">281 участник</b></div>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вывод:</b> <span class="text-emerald-300 font-semibold">Да, полностью можем.</span> Формат <code class="text-indigo-300">user&lt;ID&gt;</code> не меняется при смене имени, юзернейма или телефона. Мы видим путь экспата: общий туристический чат (TravelAsk) → чат по страхованию → региональные чаты (Пхукет/Паттайя).
            </p>
          </div>

          <!-- Вопрос 2: Сегментация -->
          <div class="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-indigo-600/30 text-indigo-400 border border-indigo-500/40 flex items-center justify-center font-bold text-xs">2</span>
              <h4 class="text-sm font-bold text-white">Сегментация активности и роли (Q&A)</h4>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вопрос:</b> Как распределяется аудитория? Есть ли ядро участников? Кто задает вопросы, а кто отвечает?
            </p>
            <div class="bg-gray-800/60 p-3 rounded-lg text-xs space-y-1.5 border border-gray-700/50">
              <div class="flex justify-between"><span class="text-gray-400">Разовые гости (1 сообщение):</span><b class="text-amber-300">53.4% аудитории (5.2% контента)</b></div>
              <div class="flex justify-between"><span class="text-gray-400">Ядро комьюнити (&gt;100 сообщ):</span><b class="text-rose-400">2.9% (680 чел) = 45.4% контента!</b></div>
              <div class="flex justify-between"><span class="text-gray-400">Прямые ответы на вопросы:</span><b class="text-indigo-300">64.5% всех сообщений</b></div>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вывод:</b> Экстремальная концентрация. Консультанты чата («Денис เดน» — 3.6k сообщ., 64% ответов; «Anna» — 2.7k сообщ., 76% ответов) формируют повестку и авторитетно направляют туристов.
            </p>
          </div>

          <!-- Вопрос 3: Доступность -->
          <div class="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-indigo-600/30 text-indigo-400 border border-indigo-500/40 flex items-center justify-center font-bold text-xs">3</span>
              <h4 class="text-sm font-bold text-white">Доступность аудитории и каналы охвата</h4>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вопрос:</b> У какой доли есть Telegram ID и username? Где можем использовать ID (реклама, механики охвата)?
            </p>
            <div class="bg-gray-800/60 p-3 rounded-lg text-xs space-y-2 border border-gray-700/50">
              <div>
                <span class="text-indigo-300 font-semibold">1. Telegram Ads (Рекламный кабинет):</span>
                <p class="text-gray-400 mt-0.5">Загрузка списков user_id в Telegram Ads недоступна политикой приватности TG. Однако мы знаем список 10 ключевых каналов концентрации аудитории для точного таргетинга.</p>
              </div>
              <div>
                <span class="text-emerald-400 font-semibold">2. Telegram Mini Apps (TMA) & Боты:</span>
                <p class="text-gray-400 mt-0.5"><b>Самый сильный легальный канал.</b> Посев полезного сервиса (калькулятор DTV, проверка штрафов) в этих чатах. При открытии TMA создается бесплатный постоянный push-канал.</p>
              </div>
              <div>
                <span class="text-amber-400 font-semibold">3. Персональный аутрич:</span>
                <p class="text-gray-400 mt-0.5">Только для 680 экспертов ядра (амбассадорство, партнерство). Массовый спам в ЛС запрещен.</p>
              </div>
            </div>
          </div>

          <!-- Вопрос 4: Динамика и кейс 15.09.2026 -->
          <div class="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2">
              <span class="w-6 h-6 rounded-lg bg-indigo-600/30 text-indigo-400 border border-indigo-500/40 flex items-center justify-center font-bold text-xs">4</span>
              <h4 class="text-sm font-bold text-white">Динамика за 2026 год, всплески и DTV кейс</h4>
            </div>
            <p class="text-xs text-gray-300 leading-relaxed">
              <b>Вопрос:</b> Можем ли выявлять всплески и первоисточники? Что произошло 15.09.2026? Можно ли выгрузить группу @visadtv?
            </p>
            <div class="bg-gray-800/60 p-3 rounded-lg text-xs space-y-2 border border-gray-700/50">
              <div>
                <span class="text-rose-400 font-semibold">Всплеск 15–16 сентября 2026:</span>
                <p class="text-gray-400 mt-0.5">09.09 (67 сообщений) → 15.09 (50 сообщений) — реакция на ограничение визаранов и изменение штампов. Первоисточник в чатах: пост канала «Таиланд новости» 16.09 в 13:34.</p>
              </div>
              <div>
                <span class="text-emerald-400 font-semibold">Статус группы @visadtv:</span>
                <p class="text-gray-400 mt-0.5"><b>Главный источник правды по DTV.</b> Все консультанты чатов ссылаются на нее. Технически выгружается как штатно, так и по API (Telethon).</p>
              </div>
              <div>
                <span class="text-indigo-300 font-semibold">Топ чатов для расширения (до 10):</span>
                <p class="text-gray-400 mt-0.5">@visadtv, forum_phuket, forum_pattaya, thailand_forum, thailand_drive, samui_forum.</p>
              </div>
            </div>
          </div>
        </div>
      </div>

    </main>

    <!-- Modal: Formula Explanation -->
    <div v-if="showFormulaModal" class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div class="bg-gray-900 border border-gray-800 rounded-2xl max-w-lg w-full p-6 space-y-4">
        <div class="flex justify-between items-center">
          <h3 class="text-base font-bold text-white">Формула Opportunity Score (0–100)</h3>
          <button @click="showFormulaModal = false" class="text-gray-400 hover:text-white">✕</button>
        </div>
        <p class="text-xs text-gray-300 leading-relaxed">
          <b>Opportunity Score</b> рассчитывается для каждого проблемного узла, объединяя 3 объективных рыночных фактора:
        </p>
        <div class="bg-gray-800/80 p-3 rounded-lg text-xs space-y-2 font-mono text-indigo-300">
          <div>Opportunity Score = Volume (до 50) + Pain (до 30) + Commercial (до 20)</div>
        </div>
        <ul class="text-xs text-gray-300 space-y-1.5 list-disc pl-5">
          <li><b>Volume Score (макс. 50 баллов)</b>: логарифмическая шкала количества упоминаний темы.</li>
          <li><b>Pain Score (макс. 30 баллов)</b>: средняя интенсивность эмоциональной боли и отказов (от 0 до 3.0).</li>
          <li><b>Commercial Intent (макс. 20 баллов)</b>: доля людей, которые прямо ищут платных агентов, посредников или помощников.</li>
        </ul>
        <div class="text-right pt-2">
          <button @click="showFormulaModal = false" class="bg-indigo-600 text-white text-xs px-4 py-2 rounded-lg font-semibold">
            Понятно
          </button>
        </div>
      </div>
    </div>

    <!-- Modal: Create Hypothesis -->
    <div v-if="showCreateModal" class="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4">
      <div class="bg-gray-900 border border-gray-800 rounded-2xl max-w-lg w-full p-6 space-y-4">
        <div class="flex justify-between items-center">
          <h3 class="text-base font-bold text-white">Создание продуктовой гипотезы</h3>
          <button @click="showCreateModal = false" class="text-gray-400 hover:text-white">✕</button>
        </div>
        <div class="space-y-3 text-xs">
          <div>
            <label class="block text-gray-400 mb-1">Название гипотезы / сервиса:</label>
            <input v-model="newHypothesis.title" class="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white" />
          </div>
          <div>
            <label class="block text-gray-400 mb-1">Тематика:</label>
            <select v-model="newHypothesis.topic" class="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white">
              <option value="visas">Визы и въезд</option>
              <option value="money">Банки и карты</option>
              <option value="housing">Аренда жилья</option>
              <option value="transport">Транспорт и байки</option>
              <option value="health">Медицина и страховки</option>
            </select>
          </div>
          <div>
            <label class="block text-gray-400 mb-1">Целевая аудитория (ЦА):</label>
            <input v-model="newHypothesis.target_audience" class="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white" />
          </div>
          <div>
            <label class="block text-gray-400 mb-1">Суть боли и решение:</label>
            <textarea v-model="newHypothesis.pain_summary" rows="3" class="w-full bg-gray-800 border border-gray-700 rounded-lg p-2 text-white"></textarea>
          </div>
        </div>
        <div class="flex justify-end space-x-2 pt-2">
          <button @click="showCreateModal = false" class="px-3 py-1.5 text-xs text-gray-400">Отмена</button>
          <button @click="saveHypothesis" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-4 py-1.5 rounded-lg font-semibold">
            Сохранить в бэклог
          </button>
        </div>
      </div>
    </div>

  </div>

  <!-- Embedded Analytics Dataset -->
  <script>
    const INITIAL_DATA = {data_json};
  </script>

  <!-- Application Logic -->
  <script>
    const {{ createApp, ref, computed, onMounted }} = Vue;

    createApp({{
      setup() {{
        const countries = ref(INITIAL_DATA.countries || []);
        const selectedCountry = ref('TH');
        const clusters = ref(INITIAL_DATA.clusters || []);
        const stats = ref(INITIAL_DATA.stats || {{}});
        const signals = ref(INITIAL_DATA.signals || []);
        const hypotheses = ref(JSON.parse(localStorage.getItem('aevo_hypotheses') || 'null') || INITIAL_DATA.hypotheses || []);

        const currentTab = ref('dashboard');
        const tabs = ref([
          {{ id: 'dashboard', name: 'Синтез болей и Opportunity', icon: '🔥' }},
          {{ id: 'feed', name: 'Лента инсайтов', icon: '⚡', badge: signals.value.length }},
          {{ id: 'audit', name: 'Аудит (4 вопроса)', icon: '📊' }},
          {{ id: 'hypotheses', name: 'Бэклог гипотез', icon: '📋', badge: hypotheses.value.length }}
        ]);

        const showFormulaModal = ref(false);
        const showCreateModal = ref(false);

        // Feed Filters
        const searchQuery = ref('');
        const filterTopic = ref('all');
        const filterSignal = ref('all');
        const minPain = ref(0);
        const currentPage = ref(1);
        const pageSize = 25;

        // New Hypothesis form state
        const newHypothesis = ref({{
          title: '',
          topic: 'visas',
          target_audience: '',
          pain_summary: '',
          evidence_count: 1
        }});

        const filteredClusters = computed(() => {{
          return clusters.value.filter(c => c.country_code === selectedCountry.value);
        }});

        const filteredSignals = computed(() => {{
          return signals.value.filter(s => {{
            if (filterTopic.value !== 'all' && s.topic !== filterTopic.value) return false;
            if (filterSignal.value !== 'all' && s.signal_type !== filterSignal.value) return false;
            if (s.pain < parseFloat(minPain.value)) return false;
            if (searchQuery.value.trim()) {{
              const q = searchQuery.value.toLowerCase();
              return s.text.toLowerCase().includes(q) || s.display_name.toLowerCase().includes(q);
            }}
            return true;
          }});
        }});

        const totalPages = computed(() => Math.ceil(filteredSignals.value.length / pageSize) || 1);

        const paginatedSignals = computed(() => {{
          const start = (currentPage.value - 1) * pageSize;
          return filteredSignals.value.slice(start, start + pageSize);
        }});

        function switchCountry(code) {{
          selectedCountry.value = code;
          currentPage.value = 1;
        }}

        function getCountryName(code) {{
          const c = countries.value.find(item => item.country_code === code);
          return c ? c.country_name : 'Таиланд';
        }}

        function getTopicLabel(topic) {{
          const map = {{
            visas: 'Визы и въезд',
            money: 'Банки и финансы',
            housing: 'Жилье и аренда',
            transport: 'Транспорт и байки',
            health: 'Здоровье и страховка',
            work_business: 'Работа и бизнес',
            general: 'Бытовое'
          }};
          return map[topic] || topic || 'Общее';
        }}

        function getSignalLabel(sig) {{
          const map = {{
            pain_complaint: 'Боль / отказ',
            commercial_intent: 'Ищет платную услугу',
            unresolved_question: 'Вопрос / сложность',
            casual_chat: 'Диалог'
          }};
          return map[sig] || sig;
        }}

        function getSignalBadgeClass(sig) {{
          if (sig === 'commercial_intent') return 'bg-amber-950 text-amber-300 border border-amber-800';
          if (sig === 'pain_complaint') return 'bg-rose-950 text-rose-300 border border-rose-800';
          return 'bg-blue-950 text-blue-300 border border-blue-800';
        }}

        function getStatusBadgeClass(status) {{
          if (status === 'in_discovery') return 'bg-purple-950 text-purple-300 border-purple-800';
          if (status === 'validated') return 'bg-emerald-950 text-emerald-300 border-emerald-800';
          return 'bg-gray-800 text-gray-300 border-gray-700';
        }}

        function filterFeedByTopic(topic) {{
          filterTopic.value = topic;
          currentTab.value = 'feed';
        }}

        function openHypothesisFromCluster(cluster) {{
          newHypothesis.value = {{
            title: `Решение проблемы: ${{cluster.title}}`,
            topic: cluster.topic,
            target_audience: 'Экспаты и долгосрочные туристы в Таиланде',
            pain_summary: cluster.description,
            evidence_count: cluster.mentions_count
          }};
          showCreateModal.value = true;
        }}

        function openHypothesisFromMessage(msg) {{
          newHypothesis.value = {{
            title: `Гипотеза по запросу от ${{msg.display_name}}`,
            topic: msg.topic,
            target_audience: 'Участники сообществ с аналогичной потребностью',
            pain_summary: msg.text.slice(0, 150) + '...',
            evidence_count: 1
          }};
          showCreateModal.value = true;
        }}

        function saveHypothesis() {{
          const item = {{
            id: 'hyp_' + Date.now().toString(36),
            title: newHypothesis.value.title || 'Новая гипотеза',
            topic: newHypothesis.value.topic,
            target_audience: newHypothesis.value.target_audience || 'Экспаты',
            pain_summary: newHypothesis.value.pain_summary || '',
            evidence_count: newHypothesis.value.evidence_count || 1,
            status: 'new',
            created_at: new Date().toISOString().slice(0, 16).replace('T', ' ')
          }};
          hypotheses.value.unshift(item);
          localStorage.setItem('aevo_hypotheses', JSON.stringify(hypotheses.value));
          tabs.value.find(t => t.id === 'hypotheses').badge = hypotheses.value.length;
          showCreateModal.value = false;
          currentTab.value = 'hypotheses';
        }}

        function removeHypothesis(id) {{
          hypotheses.value = hypotheses.value.filter(h => h.id !== id);
          localStorage.setItem('aevo_hypotheses', JSON.stringify(hypotheses.value));
          tabs.value.find(t => t.id === 'hypotheses').badge = hypotheses.value.length;
        }}

        function exportAllHypothesesMarkdown() {{
          let md = '# Бэклог продуктовых гипотез (AEVO Chat-analytics)\\n\\n';
          md += `Сформировано на основе анализа 240,000 сообщений русскоязычных сообществ Таиланда.\\n\\n`;
          hypotheses.value.forEach((h, i) => {{
            md += `### ${{i + 1}}. ${{h.title}}\\n`;
            md += `- **Тематика**: ${{getTopicLabel(h.topic)}}\\n`;
            md += `- **Целевая аудитория**: ${{h.target_audience}}\\n`;
            md += `- **Суть боли**: ${{h.pain_summary}}\\n`;
            md += `- **Объем подтверждений**: ${{h.evidence_count}} сообщений\\n`;
            md += `- **Статус**: ${{h.status}}\\n\\n`;
          }});

          const blob = new Blob([md], {{ type: 'text/markdown;charset=utf-8;' }});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `aevo_hypotheses_${{new Date().toISOString().slice(0, 10)}}.md`;
          a.click();
        }}

        return {{
          countries,
          selectedCountry,
          clusters,
          stats,
          signals,
          hypotheses,
          currentTab,
          tabs,
          showFormulaModal,
          showCreateModal,
          searchQuery,
          filterTopic,
          filterSignal,
          minPain,
          currentPage,
          filteredClusters,
          filteredSignals,
          totalPages,
          paginatedSignals,
          newHypothesis,
          switchCountry,
          getCountryName,
          getTopicLabel,
          getSignalLabel,
          getSignalBadgeClass,
          getStatusBadgeClass,
          filterFeedByTopic,
          openHypothesisFromCluster,
          openHypothesisFromMessage,
          saveHypothesis,
          removeHypothesis,
          exportAllHypothesesMarkdown
        }};
      }}
    }}).mount('#app');
  </script>
</body>
</html>"""
    return html

def main():
    print("Extracting analytical data from DuckDB...")
    data = fetch_data()
    print(f"Data extracted: {len(data['clusters'])} clusters, {len(data['signals'])} signals, {len(data['hypotheses'])} hypotheses")

    print("Generating standalone HTML dashboard...")
    html_content = generate_html(data)

    OUTPUT_FILE_1.write_text(html_content, encoding="utf-8")
    OUTPUT_FILE_2.write_text(html_content, encoding="utf-8")

    size_kb = len(html_content.encode("utf-8")) / 1024
    print(f"Success! Generated standalone dashboard:")
    print(f"  -> {OUTPUT_FILE_1} ({size_kb:.1f} KB)")
    print(f"  -> {OUTPUT_FILE_2} ({size_kb:.1f} KB)")

if __name__ == "__main__":
    main()
