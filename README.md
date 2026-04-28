# Blending Optimization System - Backend

This is the backend service for the Oil & Gas Blending Optimization application. It provides a REST API to manage terminals, parse Excel data, and run AI-assisted blending calculations.

## 🚀 Key Features

- **AI-Assisted Blending**: Integration with OpenAI (GPT-4o) to interpret user requirements in natural language (Azerbaijani/English) and translate them into optimization constraints.
- **Optimization Engine**: Uses `scipy.optimize` to find the most cost-effective blend that meets all physical constraints (Viscosity, Sulphur, Density, etc.).
- **Excel Data Extraction**: Automatically parses complex "BLEND CALCULATION" Excel sheets to identify tank stock levels and property values.
- **Terminal Management**: Supports multiple loading terminals, each with its own inventory data.
- **Chat History**: Persists user conversations and calculation results in a relational database.

## 🛠️ Technology Stack

- **Framework**: Django / Django REST Framework
- **Optimization**: Scipy, Numpy
- **AI**: OpenAI SDK
- **Excel Processing**: Openpyxl, Pandas
- **Database**: SQLite (Local) / PostgreSQL (Production)

## 📁 Project Structure

```text
backend/
├── blending_project/    # Project configuration (settings, urls, wsgi)
├── core/                # Main application logic
│   ├── models.py        # Database schemas (Terminals, Chat, Messages)
│   ├── views.py         # API endpoints and AI orchestration
│   └── services/
│       ├── blending.py  # Optimization engine
│       └── excel.py     # Excel parsing service
├── uploads/             # Global storage for fallback data
├── terminals/           # Storage for terminal-specific Excel files
├── requirements.txt     # Python dependencies
└── .env                 # Environment variables (API keys)
```

## ⚙️ Setup & Installation

### 1. Prerequisites
- Python 3.10 or higher
- `pip` (Python package manager)

### 2. Create a Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Create a `.env` file in the `backend/` directory:
```env
OPENAI_API_KEY=your_openai_api_key_here
DEV=development
```

### 5. Run Database Migrations
```bash
python manage.py migrate
```

### 6. Start the Development Server
```bash
python manage.py runserver
```

The API will be available at `http://127.0.0.1:8000/api/`.

## 🧪 Running Tests (Optional)
Standalone test scripts have been removed to keep the production folder clean. Core unit tests (if any) can be run via:
```bash
python manage.py test
```

## 📄 License
Internal corporate use only. All rights reserved.
