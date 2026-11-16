import uuid
from datetime import datetime

from pydantic import BaseModel


class SentenceRead(BaseModel):
    id: int
    start: float
    end: float
    text: str
    first_word: int
    last_word: int


class WordRead(BaseModel):
    start: float
    end: float
    text: str
    probability: float


class TranscriptRead(BaseModel):
    job_id: uuid.UUID
    language: str
    language_probability: float
    duration_seconds: float
    model: str
    word_count: int
    sentence_count: int
    sentences: list[SentenceRead]
    words: list[WordRead] | None = None  # only with ?include_words=true
    created_at: datetime
