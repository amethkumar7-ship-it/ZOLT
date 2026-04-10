# ZOLT 📊

A Flask-based trading analytics platform with a dashboard, stock screener, and AI chat features.

## Features

- 📈 **Dashboard** – Visual overview of trading performance and portfolio data
- 🔍 **Screener** – Filter and scan stocks based on custom criteria
- 🤖 **AI Chat** – Ask trading-related questions powered by DeepSeek AI
- 🗂️ **JSON Input Processing** – Processes DeepSeek JSON data inputs

## Tech Stack

- **Backend**: Python, Flask
- **Frontend**: HTML, CSS, JavaScript
- **AI**: DeepSeek JSON integration

## Project Structure

```
Mobaextreme/
├── app.py                  # Main Flask application
├── templates/
│   ├── index.html          # Home page
│   ├── dashboard.html      # Trading dashboard
│   ├── screener.html       # Stock screener
│   └── chat.html           # AI chat interface
└── Input/                  # DeepSeek JSON data files
```

## Getting Started

```bash
# Install dependencies
pip install flask

# Run the app
python app.py
```

Then open `http://localhost:5000` in your browser.

## Author

**amethkumar7** – [GitHub](https://github.com/amethkumar7-ship-it)
