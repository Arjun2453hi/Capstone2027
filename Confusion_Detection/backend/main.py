"""
Minimal FastAPI backend using Groq.

Endpoints:
  POST /chat
  POST /process
  GET  /history
  DELETE /history

Run:
  pip install fastapi uvicorn groq python-dotenv
  uvicorn main:app --reload --port 8000
"""

import os
import uuid
from datetime import datetime
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="Capstone Chat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """
You are a helpful assistant for a computer science student studying
Software Engineering.

Answer clearly and concisely.
If information is unavailable, say so.
"""

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)

chat_history: List[dict] = []

# --------------------------------------------------
# SCHEMAS
# --------------------------------------------------

class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    id: str
    role: str
    content: str
    timestamp: str


class ProcessRequest(BaseModel):
    pdf_path: str | None = None
    questions_path: str | None = None


class ProcessResponse(BaseModel):
    status: str
    message: str


# --------------------------------------------------
# GROQ CALL
# --------------------------------------------------

def call_llm(messages: List[dict]) -> str:

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.3,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            *messages,
        ],
    )

    return response.choices[0].message.content


# --------------------------------------------------
# CHAT
# --------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):

    if not req.message.strip():
        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty."
        )

    chat_history.append(
        {
            "role": "user",
            "content": req.message
        }
    )

    try:
        reply = call_llm(chat_history)

    except Exception as e:
        chat_history.pop()
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )

    chat_history.append(
        {
            "role": "assistant",
            "content": reply
        }
    )

    return ChatResponse(
        id=str(uuid.uuid4()),
        role="assistant",
        content=reply,
        timestamp=datetime.utcnow().isoformat(),
    )


# --------------------------------------------------
# HISTORY
# --------------------------------------------------

@app.get("/history", response_model=List[ChatResponse])
def get_history():

    return [
        ChatResponse(
            id=str(uuid.uuid4()),
            role=m["role"],
            content=m["content"],
            timestamp="",
        )
        for m in chat_history
    ]


@app.delete("/history")
def clear_history():

    chat_history.clear()

    return {
        "status": "cleared"
    }


# --------------------------------------------------
# PROCESS
# --------------------------------------------------

@app.post("/process", response_model=ProcessResponse)
def process(req: ProcessRequest):

    pdf_path = req.pdf_path or r"C:\Users\NAGARJUN N H\OneDrive\Desktop\Capstone\Capstone2027\Data\se-u2-slides.pdf"

    questions_path = (
        req.questions_path
        or r"C:\Users\NAGARJUN N H\OneDrive\Desktop\Capstone\Capstone2027\Data\u2_questions.txt"
    )

    if not os.path.exists(pdf_path):
        raise HTTPException(
            status_code=404,
            detail=f"PDF not found: {pdf_path}"
        )

    if not os.path.exists(questions_path):
        raise HTTPException(
            status_code=404,
            detail=f"Questions file not found: {questions_path}"
        )

    return ProcessResponse(
        status="accepted",
        message=(
            f"Pipeline queued for "
            f"'{os.path.basename(pdf_path)}' "
            f"with "
            f"'{os.path.basename(questions_path)}'"
        ),
    )


@app.get("/")
def root():

    return {
        "status": "ok",
        "provider": "groq",
        "model": MODEL,
    }