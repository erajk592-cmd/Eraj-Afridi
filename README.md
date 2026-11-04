# 📊 AI Student Attendance Analyzer

A beginner-friendly **Streamlit** web app for managing student attendance, analysing it with **Pandas / NumPy**, visualising it with **Plotly**, and getting plain-English insights from **Google Gemini**. All data is stored in a local **SQLite** database.

## ✨ Main Features

- **Dashboard** – total students, classes, present / absent counts, overall attendance %, low-attendance students, and interactive charts.
- **Student management** – add, view, search, edit, and delete students (unique student IDs enforced).
- **Attendance marking** – pick a date and class, mark **Present / Late / Absent**, save, and re-open the same day to update it. No duplicate entries per student and date.
- **Attendance records** – search by name or ID; filter by class, date, and status.
- **Analysis** – per-student %, class-wise %, daily and monthly reports, present/absent day counts, and low-attendance detection (adjustable threshold).
- **Charts** – present vs absent, monthly trends, class comparison, and individual student charts.
- **AI assistant (Gemini)** – attendance summaries, trends, explanation of low attendance, improvement ideas, and free-form questions. The AI only receives statistics calculated from your real data.
- **CSV / Excel** – import with validation and clear error messages; export reports as CSV or a multi-sheet Excel workbook.

> **How attendance % is calculated:** `(Present + Late) ÷ total recorded days × 100`. Late counts as attended.

## 🧰 Technology Stack

| Tool | Purpose |
|------|---------|
| Python 3.10+ | Main language |
| Streamlit | Web UI and dashboard |
| SQLite (`sqlite3`) | Local database |
| Pandas / NumPy | Data analysis |
| Plotly | Interactive charts |
| Google Gemini (`google-genai`) | AI analysis, via Google AI Studio |
| GitHub | Code hosting |
| Streamlit Community Cloud | Free deployment |

## 📦 Required Libraries

Listed in `requirements.txt`: `streamlit`, `pandas`, `numpy`, `plotly`, `google-genai`, `openpyxl`, `python-dateutil`.
Built-in modules used: `sqlite3`, `os`, `io`, `re`, `datetime`.

## 📁 Project Folder Structure

```
attendance-analyzer/
├── app.py                  # The whole application
├── requirements.txt        # Python dependencies
├── README.md               # This file
├── .gitignore              # (create this) keeps secrets and the database out of GitHub
├── .streamlit/
│   └── secrets.toml        # (create this, local only) your Gemini API key
└── attendance.db           # Created automatically on first run
```

Create a `.gitignore` file containing:

```
.streamlit/secrets.toml
attendance.db
__pycache__/
venv/
```

## 🚀 Installation

1. Install **Python 3.10 or newer** and (optionally) Git.
2. Put `app.py`, `requirements.txt`, and `README.md` in a folder, e.g. `attendance-analyzer`.
3. Open a terminal in that folder and create a virtual environment:

   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS / Linux:
   source venv/bin/activate
   ```

4. Install the libraries:

   ```bash
   pip install -r requirements.txt
   ```

## ▶️ Run the App

```bash
streamlit run app.py
```

The app opens at http://localhost:8501. The database file `attendance.db` and its tables are created automatically on first start.

## 🔑 Configure the Gemini API Key

The AI assistant is optional; everything else works without a key.

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and create an API key.
2. **Locally:** create `.streamlit/secrets.toml` in the project folder:

   ```toml
   GEMINI_API_KEY = "paste-your-key-here"
   ```

   Alternatively set an environment variable instead:

   ```bash
   # macOS / Linux
   export GEMINI_API_KEY="paste-your-key-here"
   # Windows PowerShell
   $env:GEMINI_API_KEY="paste-your-key-here"
   ```

3. Restart the app.

Optional: set `GEMINI_MODEL` (in secrets or as an environment variable) to use a different Gemini model. The default is `gemini-2.5-flash`.

**Never commit your key to GitHub.** The app never hardcodes it. By default, student **names are not sent** to Gemini (only IDs); you can switch this on in the AI Assistant page if you are permitted to share names.

## ⬆️ How to Upload Attendance Data

Go to **Import / Export → Import attendance** and upload a `.csv` or `.xlsx` file (max 5 MB). Download the sample template from the same page.

| Student ID | Name | Class | Date | Status |
|-----------|------|-------|------|--------|
| S001 | Ayesha Khan | 10-A | 2026-09-01 | Present |
| S002 | Bilal Ahmed | 10-A | 2026-09-01 | Absent |
| S003 | Sara Ali | 10-B | 2026-09-01 | Late |

- **Required columns:** Student ID, Date, Status. Common alternatives like *Roll No* or *Section* are recognised.
- **Name and Class** are required only for students not registered yet; those students are created automatically.
- **Status** values: Present, Absent, Late (or P / A / L).
- **Dates:** use `YYYY-MM-DD`. Ambiguous formats like `03/09/2026` are read month-first (March 9).
- Rows with problems are listed with their row number and skipped; valid rows can still be imported.
- Importing a student and date that already exist **updates** the record instead of duplicating it.

## ☁️ Deploy on Streamlit Community Cloud

1. Push the project to GitHub (see below).
2. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub.
3. Click **Create app**, choose your repository and branch, and set **Main file path** to `app.py`.
4. Open **Advanced settings → Secrets** and paste:

   ```toml
   GEMINI_API_KEY = "paste-your-key-here"
   ```

5. Click **Deploy**.

> ⚠️ **Important:** Streamlit Community Cloud storage is temporary. The SQLite file can reset when the app restarts or redeploys. Use **Import / Export → Export reports** regularly to back up your data, and re-import it when needed. For permanent storage, use a hosted database.

## 🐙 Upload the Project to GitHub

1. Create a new empty repository on [github.com](https://github.com/new) (e.g. `attendance-analyzer`).
2. In your project folder run:

   ```bash
   git init
   git add app.py requirements.txt README.md .gitignore
   git commit -m "Initial commit: AI Student Attendance Analyzer"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/attendance-analyzer.git
   git push -u origin main
   ```

3. Check that `.streamlit/secrets.toml` and `attendance.db` do **not** appear in the repository.

## 🛠️ Troubleshooting

| Problem | Fix |
|--------|-----|
| "Gemini API key not found" | Add `GEMINI_API_KEY` to secrets or environment variables and restart. |
| Gemini request fails | Check the key, internet connection, and your free-tier quota in Google AI Studio. |
| Import says a column is missing | Make sure the header row contains Student ID, Date, and Status. |
| Data disappeared on Streamlit Cloud | Cloud storage is temporary; export backups and re-import. |

Built as a learning project. Attendance data may include personal information, so follow your institution's privacy rules.# Eraj-Afridi
