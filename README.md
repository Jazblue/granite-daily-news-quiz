# Granite Daily News Quiz

A small portfolio project that uses local Granite 4 3B through Ollama to generate five current-affairs multiple-choice questions and publishes them to GitHub Pages.

- **Website:** https://jazblue.github.io/granite-daily-news-quiz/
- **Repository:** https://github.com/Jazblue/granite-daily-news-quiz
- **Model:** `granite4:3b` via the local Ollama CLI
- **Input:** BBC News RSS front-page feed
- **Output:** `data/quiz.json`
- **Publishing:** authenticated Git push via GitHub CLI; no token in source code

## Important BBC content-use note

This is a learning prototype. The BBC's terms place limits on extracting metadata, modifying or summarising BBC content, and using feeds for business purposes. Before publicly publishing AI-generated quiz content derived from BBC RSS, check the current terms and obtain permission where required. The project includes prominent source links and does not republish full articles. If permission is not appropriate for your use, switch to a source whose licence expressly permits this type of transformation and republication.

## Requirements

- Windows 10/11 or another OS with Python 3.10+
- Ollama installed and running locally
- Model downloaded: `ollama pull granite4:3b`
- Git installed and this repository cloned locally
- GitHub CLI installed and authenticated: `gh auth login`
- Configure Git to use GitHub CLI credentials: `gh auth setup-git`

The generator intentionally uses the direct `ollama run --nowordwrap granite4:3b` route used by the successful local Granite test. It does not use the Hermes `ollama-launch` route.

## First run

From the cloned repository folder in PowerShell:

```powershell
gh auth status
gh auth setup-git
ollama list
python .\scripts\generate_quiz.py
```

The script validates the model's JSON output before replacing the quiz, then commits and pushes only `data/quiz.json`. If any step fails, it exits with code 1 and records a traceback in `logs/quiz-run.log`. The log directory is intentionally not committed.

## Schedule every morning on Windows

After a successful manual test, create a Windows Task Scheduler task:
1. Trigger: Daily at 07:00 (local time).
2. Action: Start a program, `python.exe` (use `where.exe python` to find the path).
3. Arguments: `scripts\generate_quiz.py`
4. Start in: the full local path of this cloned repository.
5. Enable “Run only when user is logged on” for the initial setup; the PC, Ollama, network, and GitHub authentication must be available.
6. Check Task Scheduler's Last Run Result and `logs\quiz-run.log` after the first scheduled run.

If Ollama is not running, the PC is asleep, the RSS is unavailable, or GitHub push fails, the task will fail visibly in its exit code and log. Email/Telegram delivery is not configured by this prototype.

## How it works

1. Reads a small set of current RSS items.
2. Prompts local Granite to create five questions in strict JSON.
3. Validates count, option structure, answer indexes, source indexes, and explanations.
4. Writes the quiz JSON and publishes it with Git using the GitHub CLI credential helper.
5. The static site loads the JSON, lets visitors select answers, scores the quiz, and reveals explanations and source links.

## Known limitations

- Small local models can still make factual mistakes. Always verify source stories.
- The structural validator cannot prove every answer is factually correct.
- The script requires a working local Ollama installation and a logged-in Windows session for the simple initial schedule.
- The current version selects from top RSS stories; it does not scrape full articles.
