# Mobile-Optimized Web IDE

A production-grade, monolithic web application using FastAPI, Jinja2 Templates, Supabase, and GitPython. The app serves as a mobile-optimized web IDE with zero-disk persistence.

## Features

- Mobile-first responsive design
- Secure 10-digit secret key authentication
- Project management (upload ZIP, clone from GitHub)
- File browsing and editing with CodeMirror integration
- Git operations (commit, push) with real-time terminal logs
- Zero-disk persistence using /tmp/sessions/
- Supabase integration for session storage
- Security measures against directory traversal attacks

## Prerequisites

- Python 3.9+
- Git installed on the system
- Access to a Supabase project (optional, app works without it too)

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up environment variables for Supabase (optional):
   ```bash
   export SUPABASE_URL="your_supabase_url"
   export SUPABASE_KEY="your_supabase_anon_key"
   ```

4. Run the application:
   ```bash
   python main.py
   ```

The application will start on `http://localhost:8000`

## Configuration

### Supabase Setup (Optional)

If you want to use Supabase for storing session data:

1. Create a Supabase account and project
2. Set up the database schema using the SQL in `database_schema.sql`
3. Add your Supabase URL and key as environment variables

### Environment Variables

- `SUPABASE_URL` - Your Supabase project URL (optional)
- `SUPABASE_KEY` - Your Supabase anon key (optional)

## Usage

1. Visit the application in your browser
2. Enter a 10-digit secret key and project name
3. Choose to either upload a ZIP file or clone from GitHub
4. Access the IDE interface to browse and edit files
5. Use the terminal to see git operation logs

## Architecture

- **Backend**: FastAPI server handling all routes and business logic
- **Frontend**: Single HTML file with embedded CSS/JS using Jinja2 templating
- **Storage**: Temporary files stored in `/tmp/sessions/{secret_key}_{project_name}/`
- **Database**: Supabase PostgreSQL for session management (optional)
- **Real-time**: WebSocket connections for terminal log streaming

## Security

- Input sanitization to prevent directory traversal
- Validation of secret key format (10 digits)
- Temporary file storage that gets cleaned periodically
- Secure git operations with proper authentication

## File Structure

```
├── main.py                 # Main FastAPI application
├── requirements.txt        # Python dependencies
├── database_schema.sql     # Supabase database schema
├── templates/
│   └── index.html          # Single-page application template
└── README.md              # This file
```

## Limitations

- Files are stored temporarily and will be wiped on server restart
- Requires Git to be installed on the system
- Supabase integration is optional but recommended for production use