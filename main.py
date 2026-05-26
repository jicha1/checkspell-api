#  pro_letter/checkspell/main.py

from pathlib import Path
from typing import List, Dict, Any
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

def load_misspellings() -> dict:
    if not MISSPELL_PATH.exists():
        return {}
    with open(MISSPELL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


COMMON_MISSPELLINGS = load_misspellings()
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


def tokenize_text(text: str) -> List[str]:
    return word_tokenize(text, engine="newmm")


def check_word(word: str):
    if should_ignore_word(word):
        return None

    if word in COMMON_MISSPELLINGS:
        return {
            "word": word,
            "suggestions": COMMON_MISSPELLINGS[word][:5]
        }

    suggestions = spell(word)

    if not suggestions:
        return None

    if suggestions[0] == word:
        return None

    cleaned_suggestions = []
    for s in suggestions:
        s = s.strip()
        if s and s != word and s not in cleaned_suggestions:
            cleaned_suggestions.append(s)

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

        # 1) เช็กจาก misspellings แบบปลอดภัยกว่าเดิม
    # ไม่จับคำผิดที่เป็นส่วนหนึ่งของคำถูก เช่น โรงแรม ไม่ควรถูกจับเป็น โรงแร
    sorted_misspellings = sorted(COMMON_MISSPELLINGS.items(), key=lambda x: len(x[0]), reverse=True)

    for wrong_word, suggestions in sorted_misspellings:
        if wrong_word in seen_words:
            continue

        if should_ignore_word(wrong_word):
            continue

        # ถ้าคำที่เจออยู่ใน custom dictionary ให้ถือว่าถูก ไม่ต้องแจ้ง
        if wrong_word in CUSTOM_WORDS:
            continue

        # จับเฉพาะกรณีที่ข้อความมีคำผิดจริง และไม่ใช่ส่วนย่อยของคำแนะนำที่ถูกต้อง
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