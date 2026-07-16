# Robot Fleet Backend

The backend is built with FastAPI and PostgreSQL, providing a high-performance REST API and WebSocket broadcast engine.

## 🛠️ Tech Stack
- **Framework**: FastAPI
- **ORM**: SQLAlchemy 2.0
- **Migrations**: Alembic
- **Testing**: pytest, httpx
- **Database**: PostgreSQL (Neon Serverless)

## 💻 Local Setup

1. **Virtual Environment**:
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate   # Windows
   source .venv/bin/activate  # macOS/Linux
   ```

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Setup**:
   ```bash
   cp .env.example .env
   # Edit .env and add your DATABASE_URL
   ```

4. **Database Migrations**:
   The application uses Alembic for schema migrations. To set up a fresh database:
   ```bash
   alembic upgrade head
   ```

5. **Run the Server**:
   ```bash
   uvicorn app.main:app --reload
   ```
   The API docs will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

## 🗄️ Database Migrations

When you modify `models.py`, you must generate a new migration script:

```bash
alembic revision --autogenerate -m "description of changes"
alembic upgrade head
```

## 🧪 Testing

The backend includes a comprehensive pytest suite using an isolated in-memory SQLite database.

```bash
pytest tests/ -v
```
