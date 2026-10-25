#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd
from rapidfuzz import fuzz
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ===================== CONFIG (Render) =====================
TOKEN = os.environ.get("TELEGRAM_TOKEN")          # ← variável do Render
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", 78))
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", 12))
# ===========================================================

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN não configurado nas Environment Variables do Render")

# Campos prioritários
PRIORITY_FIELDS = [
    "nome", "name", "full_name", "razao_social", "nome_completo",
    "cpf", "cnpj", "rg", "cnh", "documento", "doc",
    "email", "e-mail", "mail",
    "telefone", "phone", "celular", "fone", "whatsapp",
    "endereco", "address", "logradouro", "rua", "bairro", "cidade", "uf", "cep",
    "placa", "veiculo", "carro", "marca", "modelo", "ano", "renavam", "chassi",
    "mae", "pai", "nascimento", "data_nascimento", "sexo", "idade"
]

def normalize_key(key: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(key).lower())

def is_cpf(value: str) -> bool:
    cleaned = re.sub(r'\D', '', str(value))
    return len(cleaned) == 11

def load_all_data() -> List[Dict[str, Any]]:
    records = []
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        return records

    for file in DATA_DIR.rglob("*"):
        try:
            if file.suffix.lower() == ".csv":
                df = pd.read_csv(file, dtype=str, low_memory=False).fillna("")
                for _, row in df.iterrows():
                    records.append({str(k): str(v) for k, v in row.to_dict().items()})

            elif file.suffix.lower() == ".json":
                with open(file, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                records.append({str(k): str(v) for k, v in item.items()})
                    elif isinstance(data, dict):
                        records.append({str(k): str(v) for k, v in data.items()})

            elif file.suffix.lower() in (".db", ".sqlite", ".sqlite3"):
                conn = sqlite3.connect(file)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
                for table in [t[0] for t in cursor.fetchall()]:
                    df = pd.read_sql_query(f"SELECT * FROM `{table}`", conn).fillna("").astype(str)
                    for _, row in df.iterrows():
                        records.append(row.to_dict())
                conn.close()
        except Exception as e:
            print(f"[!] Erro em {file.name}: {e}")

    print(f"[+] {len(records)} registros carregados")
    return records

ALL_RECORDS = load_all_data()

def search(query: str) -> List[Dict[str, Any]]:
    query = query.strip()
    if not query:
        return []

    if is_cpf(query):
        cpf_clean = re.sub(r'\D', '', query)
        results = []
        for rec in ALL_RECORDS:
            for v in rec.values():
                if re.sub(r'\D', '', str(v)) == cpf_clean:
                    results.append(rec)
                    break
        return results[:MAX_RESULTS]

    query_lower = query.lower()
    scored = []

    for rec in ALL_RECORDS:
        best_score = 0
        for k, v in rec.items():
            k_norm = normalize_key(k)
            if any(x in k_norm for x in ["nome", "name", "razao", "pessoa", "full"]):
                score = fuzz.token_set_ratio(query_lower, str(v).lower())
                if score > best_score:
                    best_score = score
        if best_score >= MIN_SCORE:
            scored.append((best_score, rec))

    scored.sort(key=lambda x: x[0], reverse=True)

    seen = set()
    unique = []
    for score, rec in scored:
        fingerprint = tuple(sorted(str(v) for v in list(rec.values())[:5]))
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(rec)
        if len(unique) >= MAX_RESULTS:
            break
    return unique

def format_pretty(rec: Dict[str, Any], index: int) -> str:
    sections = {
        "👤 Identidade": [],
        "📄 Documentos": [],
        "📞 Contato": [],
        "🏠 Endereço": [],
        "🚗 Veículos": [],
        "📌 Outros": []
    }

    def add_field(section: str, label: str, value: str):
        if value and value.lower() not in ("nan", "none", "null", ""):
            sections[section].append(f"  <b>{label}</b>: <code>{value}</code>")

    for k, v in rec.items():
        k_norm = normalize_key(k)
        v = str(v).strip()
        if not v or v.lower() in ("nan", "none", "null"):
            continue

        if any(x in k_norm for x in ["nome", "name", "razao", "pessoa"]):
            add_field("👤 Identidade", k, v)
        elif any(x in k_norm for x in ["cpf", "cnpj", "rg", "cnh", "documento", "doc", "titulo"]):
            add_field("📄 Documentos", k, v)
        elif any(x in k_norm for x in ["email", "mail", "telefone", "phone", "celular", "fone", "whatsapp"]):
            add_field("📞 Contato", k, v)
        elif any(x in k_norm for x in ["endereco", "address", "logradouro", "rua", "bairro", "cidade", "uf", "cep", "complemento"]):
            add_field("🏠 Endereço", k, v)
        elif any(x in k_norm for x in ["placa", "veiculo", "carro", "marca", "modelo", "ano", "renavam", "chassi", "cor"]):
            add_field("🚗 Veículos", k, v)
        else:
            add_field("📌 Outros", k, v)

    lines = [f"<b>════ Resultado {index} ════</b>"]
    for section_name, items in sections.items():
        if items:
            lines.append(f"\n{section_name}")
            lines.extend(items)

    return "\n".join(lines)

# ===================== HANDLERS =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "╔══════════════════════╗\n"
        "║   <b>DOX LOCAL BOT</b>   ║\n"
        "╚══════════════════════╝\n\n"
        "Busca em dumps locais por:\n"
        "• Nome completo\n"
        "• CPF\n\n"
        "<b>Como usar:</b>\n"
        "<code>/buscar João da Silva</code>\n"
        "<code>/buscar 123.456.789-00</code>\n\n"
        "Ou só manda o nome/CPF direto."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "Manda o nome ou CPF.\nEx: <code>/buscar Maria Oliveira</code>",
            parse_mode=ParseMode.HTML
        )
        return

    status = await update.message.reply_text(
        f"🔍 Procurando por <b>{query}</b>...",
        parse_mode=ParseMode.HTML
    )

    results = search(query)

    if not results:
        await status.edit_text("❌ Nada encontrado nos dumps locais.")
        return

    await status.edit_text(
        f"✅ Encontrei <b>{len(results)}</b> resultado(s) para <b>{query}</b>",
        parse_mode=ParseMode.HTML
    )

    for i, rec in enumerate(results, 1):
        text = format_pretty(rec, i)
        if len(text) > 4000:
            text = text[:3900] + "\n\n... (resultado cortado)"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def texto_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if len(text) < 3:
        return
    context.args = text.split()
    await buscar(update, context)

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buscar", buscar))
    app.add_handler(CommandHandler("search", buscar))
    app.add_handler(CommandHandler("cpf", buscar))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_livre))

    print("[+] Bot rodando no Render...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
