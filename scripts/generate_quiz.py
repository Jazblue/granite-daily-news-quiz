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
    # Keep source URLs with every item and request strict JSON for predictable rendering.
    prompt = """You create a short general-knowledge current-affairs quiz for a personal learning project.
Use only the facts supported by the supplied story titles and short feed descriptions. Do not invent details.
If a fact cannot be answered reliably from this evidence, skip that story and use another.
Return ONLY valid JSON with this exact shape:
{"questions":[{"question":"...","options":["...","...","...","..."],"answer_index":0,"explanation":"One-sentence explanation grounded in the evidence.","source_index":0}]}
Create exactly five distinct multiple-choice questions. Each must have exactly four plausible options, one correct answer, and answer_index as integer 0-3. Avoid questions that simply ask for a headline's exact wording. Do not include markdown or any text outside JSON.
Evidence stories (source_index refers to the zero-based story index):
""" + json.dumps(stories, ensure_ascii=False)
    # Retry generation when a small local model emits malformed JSON.
    # Only continue to validation/publishing after a response parses successfully.
    data = None
    last_error = None
    for attempt in range(1, 4):
        retry_prompt = prompt
        if attempt > 1:
            retry_prompt += (
                "\\n\\nIMPORTANT: Your previous response was invalid JSON. "
                "Return one complete, syntactically valid JSON object only. "
                "Use double quotes, escape quotes inside strings, and do not truncate it."
            )
            logging.warning("Retrying Granite JSON generation (%s/3).", attempt)
        result = run(
            ["ollama", "run", "--nowordwrap", MODEL],
            input_text=retry_prompt,
            timeout=TIMEOUT_SECONDS,
        )
        output = result.stdout.strip()
        if not output:
            last_error = "Ollama returned empty output."
            continue
        # Tolerate accidental surrounding code fences.
        output = re.sub(r"^\\s*```(?:json)?\\s*|\\s*```\\s*$", "", output, flags=re.I)
        start, end = output.find("{"), output.rfind("}")
        if start < 0 or end <= start:
            last_error = "Granite did not return a JSON object."
            continue
        try:
            data = json.loads(output[start:end+1])
            break
        except json.JSONDecodeError as exc:
            last_error = f"Granite returned malformed JSON: {exc}"
            logging.warning("Invalid JSON from Granite on attempt %s/3: %s", attempt, exc)
    if data is None:
        raise RuntimeError(f"Granite failed to return valid JSON after 3 attempts. Last error: {last_error}")
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
