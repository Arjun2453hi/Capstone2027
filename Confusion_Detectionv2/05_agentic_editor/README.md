# 05_agentic_editor — running the demo

From a fresh clone of this branch:

```bash
cd Confusion_Detectionv2
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # optional: put GROQ_API_KEY=... in it
.venv/bin/streamlit run 05_agentic_editor/app.py
```

The browser opens at http://localhost:8501. Everything except suggesting
slides works with no key: the gaps, the agent's reports, the questions,
the slide images and the broken-layout check all come from files in the
repo. To suggest slides you need a Groq API key (free at
https://console.groq.com/keys) -- put it in `.env`, or paste it into
the sidebar when the app asks.

The "Open it in PowerPoint" button works on a Mac with PowerPoint
installed; anywhere else, use "Download the edited deck".

See CLAUDE.md in this folder for how the editor works.
