#  pro_letter/checkspell/main.py

from pathlib import Path
from typing import List
import json
import re

from fastapi import FastAPI
from pydantic import BaseModel
from pythainlp.tokenize import word_tokenize
from pythainlp.spell import spell
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = Path(__file__).resolve().parent
CUSTOM_DICT_PATH = BASE_DIR / "custom_dict.txt"
MISSPELL_PATH = BASE_DIR / "common_misspellings.json"


def load_custom_dict() -> set[str]:
    if not CUSTOM_DICT_PATH.exists():
        return set()

    words = set()
    with open(CUSTOM_DICT_PATH, "r", encoding="utf-8") as f:
        for line in f:
            word = line.strip()
            if word:
                words.add(word)
    return words


CUSTOM_WORDS = load_custom_dict()


def is_thai_word(word: str) -> bool:
    return bool(re.search(r"[ก-๙]", word))


def should_ignore_word(word: str) -> bool:
    if not word:
        return True

    word = word.strip()

    if not word:
        return True

    # ข้ามตัวเลข
    if re.fullmatch(r"[0-9]+([.,][0-9]+)?", word):
        return True

    # ข้ามวันที่/รหัส/ทะเบียน/คำอังกฤษล้วน
    if re.fullmatch(r"[A-Za-z0-9\-/_.]+", word):
        return True

    # ข้ามคำสั้นมาก
    if len(word) <= 1:
        return True

    # ข้ามถ้าเป็นคำใน custom dictionary
    if word in CUSTOM_WORDS:
        return True

    # ข้ามถ้าไม่มีตัวอักษรไทย
    if not is_thai_word(word):
        return True

    return False


def clean_suggestions(word: str, suggestions) -> List[str]:
    """กรองคำแนะนำที่ไม่ควรแสดง เช่น คำแนะนำที่เหมือนคำเดิม"""
    if not isinstance(suggestions, list):
        suggestions = [suggestions]

    cleaned = []
    for suggestion in suggestions:
        suggestion = str(suggestion).strip()
        if not suggestion:
            continue
        # ห้ามแสดงคำเดิมเป็นคำแนะนำ เพราะจะทำให้คำถูกถูกมองว่าเป็นคำผิด
        if suggestion == word:
            continue
        if suggestion not in cleaned:
            cleaned.append(suggestion)
    return cleaned


def load_misspellings() -> dict:
    if not MISSPELL_PATH.exists():
        return {}

    with open(MISSPELL_PATH, "r", encoding="utf-8") as f:
        raw_misspellings = json.load(f)

    misspellings = {}
    for wrong_word, suggestions in raw_misspellings.items():
        wrong_word = str(wrong_word).strip()
        if not wrong_word:
            continue

        # คำใน custom_dict ถือว่าเป็นคำถูก ไม่ต้องใช้เป็นคำผิด
        if wrong_word in CUSTOM_WORDS:
            continue

        cleaned = clean_suggestions(wrong_word, suggestions)
        if not cleaned:
            continue

        misspellings[wrong_word] = cleaned

    return misspellings


COMMON_MISSPELLINGS = load_misspellings()
# เตรียมรายการไว้ครั้งเดียว ไม่ต้อง sort ใหม่ทุก request ช่วยลดเวลาตรวจข้อความยาว
SORTED_MISSPELLINGS = sorted(COMMON_MISSPELLINGS.items(), key=lambda x: len(x[0]), reverse=True)

app = FastAPI(title="Thai Spell Check API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:80",
        "http://127.0.0.1:80",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SpellCheckRequest(BaseModel):
    field: str
    text: str


def tokenize_text(text: str) -> List[str]:
    return word_tokenize(text, engine="newmm")


def check_word(word: str):
    word = word.strip()

    if should_ignore_word(word):
        return None

    if word in COMMON_MISSPELLINGS:
        suggestions = clean_suggestions(word, COMMON_MISSPELLINGS[word])
        if not suggestions:
            return None
        return {
            "word": word,
            "suggestions": suggestions[:5]
        }

    suggestions = spell(word)

    if not suggestions:
        return None

    if suggestions[0] == word:
        return None

    cleaned_suggestions = clean_suggestions(word, suggestions)

    if not cleaned_suggestions:
        return None

    return {
        "word": word,
        "suggestions": cleaned_suggestions[:5]
    }


@app.get("/")
def root():
    return {"message": "Thai Spell Check API is running"}


@app.post("/api/spell-check")
def api_spell_check(payload: SpellCheckRequest):
    text = payload.text.strip()

    if not text:
        return {
            "checked": True,
            "hasError": False,
            "errors": []
        }

    found_errors = []
    seen_words = set()

    # 1) เช็กจาก common_misspellings แบบปลอดภัย
    # ไม่จับคำถูกเป็นคำผิด และไม่จับคำผิดที่เป็นส่วนหนึ่งของคำถูก เช่น โรงแรม ไม่ควรถูกจับเป็น โรงแร
    for wrong_word, suggestions in SORTED_MISSPELLINGS:
        if wrong_word in seen_words:
            continue

        if should_ignore_word(wrong_word):
            continue

        suggestions = clean_suggestions(wrong_word, suggestions)
        if not suggestions:
            continue

        if wrong_word in text:
            is_part_of_correct_suggestion = any(
                wrong_word != correct_word and wrong_word in correct_word and correct_word in text
                for correct_word in suggestions
            )

            if is_part_of_correct_suggestion:
                continue

            found_errors.append({
                "wrongWord": wrong_word,
                "suggestions": suggestions[:5]
            })
            seen_words.add(wrong_word)

    # 2) tokenize แล้วเช็กทีละคำ
    tokens = tokenize_text(text)

    for token in tokens:
        token = token.strip()
        if token in seen_words:
            continue

        result = check_word(token)
        if result:
            found_errors.append({
                "wrongWord": result["word"],
                "suggestions": result["suggestions"]
            })
            seen_words.add(result["word"])

    return {
        "checked": True,
        "hasError": len(found_errors) > 0,
        "errors": found_errors
    }


# uvicorn checkspell.main:app --reload --host 127.0.0.1 --port 8001
# ต้องใช้ใน git bash


# Start Command บน Render:
# uvicorn main:app --host 0.0.0.0 --port $PORT