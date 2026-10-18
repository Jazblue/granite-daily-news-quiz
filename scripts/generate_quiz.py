#!/usr/bin/env python3
"""Generate a five-question quiz from BBC News RSS using local Ollama/Granite.

Requires Python 3.10+, Ollama, granite4:3b, Git, and GitHub CLI (gh).
For local Windows use, authenticate with 'gh auth login' and run 'gh auth setup-git'.
This script never embeds or writes a GitHub token.
"""
from __future__ import annotations
import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "quiz.json"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "quiz-run.log"
RSS_URL = "https://feeds.bbci.co.uk/news/rss.xml"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MODEL = os.environ.get("OLLAMA_MODEL", "granite4:3b")
MAX_STORIES = 12
QUESTION_COUNT = 5
TIMEOUT_SECONDS = 240

def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)])

def run(cmd, *, timeout=TIMEOUT_SECONDS, check=True, cwd=ROOT, input_text=None):
    return subprocess.run(cmd, cwd=cwd, input=input_text, text=True, encoding="utf-8",
                          errors="replace", capture_output=True, timeout=timeout, check=check)

def fetch_stories():
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "GraniteDailyNewsQuiz/1.0 (personal learning project)", "Accept": "application/rss+xml, application/xml"})
    with urllib.request.urlopen(req, timeout=25) as response:
        xml_data = response.read()
    root = ET.fromstring(xml_data)
    stories = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        description = " ".join(description.split())
        published = (item.findtext("pubDate") or "").strip()
        if title and link and link.startswith("https://"):
            stories.append({"title": title, "description": description[:500], "url": link, "published": published})
        if len(stories) >= MAX_STORIES:
            break
    if len(stories) < QUESTION_COUNT:
        raise RuntimeError(f"RSS returned only {len(stories)} usable stories; need at least {QUESTION_COUNT}.")
    return stories

def generate(stories):
    prompt = """You create a short general-knowledge current-affairs quiz for a personal learning project.
Use only facts supported by the supplied story titles and short feed descriptions. Do not invent details.
If a fact cannot be answered reliably from this evidence, make a question from another story.
Create exactly five distinct multiple-choice questions. Each question needs exactly four plausible options, one correct answer, an integer answer_index from 0 to 3, a one-sentence explanation, and a source_index referring to the zero-based evidence story index.
Avoid questions that simply ask for a headline's exact wording.
The response must follow the supplied JSON schema.
Evidence stories:
""" + json.dumps(stories, ensure_ascii=False)

    # Ollama's native structured-output mode constrains the model to the required JSON schema.
    schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": QUESTION_COUNT,
                "maxItems": QUESTION_COUNT,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "options": {
                            "type": "array", "minItems": 4, "maxItems": 4,
                            "items": {"type": "string"}
                        },
                        "answer_index": {"type": "integer", "minimum": 0, "maximum": 3},
                        "explanation": {"type": "string"},
                        "source_index": {
                            "type": "integer", "minimum": 0, "maximum": len(stories) - 1
                        }
                    },
                    "required": ["question", "options", "answer_index", "explanation", "source_index"],
                    "additionalProperties": False
                }
            }
        },
        "required": ["questions"],
        "additionalProperties": False
    }

    data = None
    last_error = None
    for attempt in range(1, 4):
        request_body = {
            "model": MODEL,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.2}
        }
        request = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            logging.info("Requesting schema-constrained JSON from Ollama (attempt %s/3).", attempt)
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                api_result = json.loads(response.read().decode("utf-8"))
            output = api_result.get("response", "").strip()
            if not output:
                last_error = "Ollama returned empty output."
                logging.warning("%s", last_error)
                continue
            data = json.loads(output)
            break
        except (json.JSONDecodeError, urllib.error.URLError, TimeoutError) as exc:
            last_error = str(exc)
            logging.warning("Structured generation attempt %s/3 failed: %s", attempt, exc)

    if data is None:
        raise RuntimeError(f"Ollama failed to return valid schema-constrained JSON after 3 attempts. Last error: {last_error}")

    questions = data.get("questions")
    if not isinstance(questions, list) or len(questions) != QUESTION_COUNT:
        raise RuntimeError(f"Granite must return exactly {QUESTION_COUNT} questions.")
    checked = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict):
            raise RuntimeError(f"Question {i+1} is not an object.")
        question = str(q.get("question", "")).strip()
        options = q.get("options")
        answer_index = q.get("answer_index")
        explanation = str(q.get("explanation", "")).strip()
        source_index = q.get("source_index")
        if not question or not isinstance(options, list) or len(options) != 4:
            raise RuntimeError(f"Question {i+1} needs a prompt and exactly four options.")
        if any(not isinstance(x, str) or not x.strip() for x in options):
            raise RuntimeError(f"Question {i+1} has an empty option.")
        if not isinstance(answer_index, int) or isinstance(answer_index, bool) or answer_index not in range(4):
            raise RuntimeError(f"Question {i+1} has invalid answer_index.")
        if not explanation:
            raise RuntimeError(f"Question {i+1} has no explanation.")
        if not isinstance(source_index, int) or isinstance(source_index, bool) or source_index not in range(len(stories)):
            raise RuntimeError(f"Question {i+1} has invalid source_index.")
        source = stories[source_index]
        checked.append({"question": question, "options": [x.strip() for x in options],
                        "answer_index": answer_index, "explanation": explanation,
                        "source_title": source["title"], "source_url": source["url"]})
    if len({q["question"].casefold() for q in checked}) != QUESTION_COUNT:
        raise RuntimeError("Granite returned duplicate questions.")
    return checked

def publish_with_git():
    # gh owns authentication; do not place credentials in source or remote URLs.
    run(["gh", "auth", "status"], timeout=30)
    run(["git", "add", "data/quiz.json"])
    staged = run(["git", "diff", "--cached", "--quiet"], check=False)
    if staged.returncode == 0:
        logging.info("Quiz file unchanged; no commit needed.")
        return
    run(["git", "commit", "-m", "Publish daily Granite news quiz"], timeout=60)
    run(["git", "push", "origin", "main"], timeout=120)
    logging.info("Pushed quiz update to GitHub.")

def main():
    setup_logging()
    started = time.monotonic()
    logging.info("START daily quiz run; model=%s; feed=%s", MODEL, RSS_URL)
    try:
        stories = fetch_stories()
        logging.info("Fetched %s RSS stories.", len(stories))
        questions = generate(stories)
        now = dt.datetime.now().astimezone()
        payload = {"date": now.date().isoformat(), "generated_at": now.isoformat(),
                   "model": MODEL, "source": "BBC News RSS",
                   "questions": questions}
        DATA_FILE.parent.mkdir(exist_ok=True)
        temp_file = DATA_FILE.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_file.replace(DATA_FILE)
        publish_with_git()
        logging.info("SUCCESS: generated %s questions and published in %.1fs.", len(questions), time.monotonic()-started)
        return 0
    except Exception as exc:
        logging.exception("FAILURE: daily quiz run failed: %s", exc)
        return 1

if __name__ == "__main__":
    sys.exit(main())
