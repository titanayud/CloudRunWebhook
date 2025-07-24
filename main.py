from flask import Flask, request, jsonify
from google.cloud import bigquery
from google.cloud import discoveryengine_v1beta
import vertexai
from vertexai.preview.generative_models import GenerativeModel

import os

app = Flask(__name__)

# Konfigurasi proyek dan lokasi
PROJECT_ID = "bumi-poc"
LOCATION = "us-central1"
DATASET = "bumi-poc.dataset_operation_bumi"
TABLE_DM = "dm_daily_operation"
TABLE_DEFINITIONS = "dm_definitions"
TABLE_ALIASES = "dm_value_aliasses"
SEARCH_APP_ID = "profil_search"

# Inisialisasi Vertex AI
def init_vertex():
    vertexai.init(project=PROJECT_ID, location=LOCATION)

# === Structured Data ===
def get_table_schema():
    client = bigquery.Client()
    table_ref = f"{DATASET}.{TABLE_DM}"
    table = client.get_table(table_ref)
    return [schema.name for schema in table.schema]

def get_definitions():
    client = bigquery.Client()
    query = f"""
        SELECT column_name, definition, example_value
        FROM `{DATASET}.{TABLE_DEFINITIONS}`
    """
    results = client.query(query).result()
    return [
        {
            "column_name": row["column_name"],
            "definition": row["definition"],
            "example_value": row["example_value"]
        }
        for row in results
    ]

def get_aliases():
    client = bigquery.Client()
    query = f"""
        SELECT column_name, alias_value, canonical_value
        FROM `{DATASET}.{TABLE_ALIASES}`
    """
    results = client.query(query).result()
    return [
        {
            "column_name": row["column_name"],
            "alias_value": row["alias_value"],
            "canonical_value": row["canonical_value"]
        }
        for row in results
    ]

rumus_text = """
Rumus-rumus penting:
- tingkat_realisasi = qty_actual / qty_budget
- persentase_realisasi = (qty_actual / qty_budget) * 100
"""

def generate_sql_with_gemini(question):
    column_names = get_table_schema()
    definitions = get_definitions()
    aliases = get_aliases()

    definitions_text = "\n".join([
        f"- {d['column_name']}: {d['definition']} (contoh: {d['example_value']})"
        for d in definitions
    ])

    aliases_text = "\n".join([
        f'- Nilai "{a["alias_value"]}" di kolom {a["column_name"]} artinya "{a["canonical_value"]}"'
        for a in aliases
    ])

    prompt = f"""
Kamu adalah asisten SQL yang cerdas dan komunikatif untuk data tambang. Tugasmu adalah:
1. Menerjemahkan pertanyaan pengguna menjadi query SQL BigQuery yang benar
2. Menjawab pertanyaan tersebut berdasarkan hasil data
3. Jika pertanyaan bersifat eksploratif (misalnya "ada berapa company?"), tampilkan jumlah serta nama-nama company tersebut
4. Jika pertanyaan bersifat eksploratif (misalnya "company mana yang produksinya tertinggi?"), tampilkan juga jumlah produksinya
5. Gunakan bahasa yang jelas, dan sesuai konteks pertambangan

Tabel sumber: {DATASET}.{TABLE_DM}

Referensi kolom:
{definitions_text}

Terminologi lain:
{aliases_text}

{rumus_text}

Sekarang buat query SQL untuk pertanyaan berikut:
\"\"\"{question}\"\"\"

Tulis hanya query-nya tanpa penjelasan.
"""
    init_vertex()
    model = GenerativeModel("gemini-2.0-flash-001")
    response = model.generate_content(prompt)
    sqlstring = response.text.strip()

    if sqlstring.startswith("```sql"):
        sqlstring = sqlstring.replace("```sql", "").replace("```", "").strip()
    elif sqlstring.startswith("```"):
        sqlstring = sqlstring.replace("```", "").strip()

    # Post-processing jika SELECT *
    if sqlstring.strip().lower().startswith("select *"):
        perusahaan = None
        for a in aliases:
            if a["alias_value"].lower() in question.lower():
                perusahaan = a["canonical_value"]
                break
        if not perusahaan:
            for d in ["KALTIM PRIMA COAL", "ARUTMIN INDONESIA"]:
                if d.lower() in question.lower():
                    perusahaan = d
                    break
        if any(k in question.lower() for k in ["jumlah", "total", "qty_actual"]):
            where_clause = f" WHERE company LIKE '%{perusahaan}%'" if perusahaan else ""
            sqlstring = f"SELECT SUM(qty_actual) as total_qty_actual FROM `{DATASET}.{TABLE_DM}`{where_clause}"
    return sqlstring

def run_query(sql):
    client = bigquery.Client()
    query_job = client.query(sql)
    results = query_job.result()
    return [dict(row) for row in results]

def generate_answer_with_gemini(question, rows):
    prompt = f"""
Kamu adalah asisten yang menjawab pertanyaan pengguna berdasarkan hasil query database.

Pertanyaan: {question}

Hasil query:
{rows}

Buat jawaban yang ramah dan mudah dimengerti untuk ditampilkan ke pengguna.
"""
    model = GenerativeModel("gemini-2.0-flash-001")
    response = model.generate_content(prompt)
    return response.text.strip()

# === Unstructured Data (Vertex AI Search App) ===
def handle_vertex_search(question):
    client = discoveryengine_v1beta.SearchServiceClient()
    serving_config = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/collections/default_collection/"
        f"dataStores/{SEARCH_APP_ID}/servingConfigs/default_search"
    )

    request = discoveryengine_v1beta.SearchRequest(
        serving_config=serving_config,
        query=question,
        page_size=3,
    )

    response = client.search(request=request)
    matched_docs = []
    for result in response.results:
        doc = result.document
        snippet = doc.derived_struct_data.get("snippet", "")
        content = doc.derived_struct_data.get("content", "")
        matched_docs.append(f"{snippet}\n{content}")

    prompt = f"""
Berikut adalah informasi yang ditemukan dari dokumen:

{chr(10).join(f"[Doc {i+1}]\n{doc}" for i, doc in enumerate(matched_docs))}

Pertanyaan:
{question}

Jawaban:
"""
    model = GenerativeModel("gemini-2.0-flash-001")
    response = model.generate_content(prompt)
    return response.text.strip()

@app.route("/", methods=["POST"])
def webhook():
    body = request.get_json()
    answer = ""
    question = ""
    sql = ""

    try:
        question = body.get("text") or body.get("fulfillmentInfo", {}).get("tag", "")
        parameters = body.get("sessionInfo", {}).get("parameters", {})
        mode = parameters.get("mode", "sql")  # default ke SQL jika tidak ditentukan
    except:
        question = "Tampilkan semua data"
        parameters = {}
        mode = "sql"

    try:
        if mode == "search":
            answer = handle_vertex_search(question)
        else:
            sql = generate_sql_with_gemini(question)
            print(f"Generated SQL:\n{sql}")
            rows = run_query(sql)
            if not rows:
                answer = "Data tidak ditemukan."
            else:
                answer = generate_answer_with_gemini(question, rows)
    except Exception as e:
        answer = f"Terjadi error: {str(e)}"

    return jsonify({
        "fulfillment_response": {
            "messages": [
                {"text": {"text": [answer]}},
                {"question": {"text": [question]}},
                {"sql": {"text": [sql] if sql else []}}
            ]
        },
        "sessionInfo": {
            "parameters": {
                "result_text": answer
            }
        }
    })

# Jalankan
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
