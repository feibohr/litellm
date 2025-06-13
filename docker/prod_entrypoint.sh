#!/bin/sh

# Function to initialize database
initialize_database() {
    if [ -n "$DATABASE_URL" ]; then
        echo "🔧 Initializing database..."
        
        # Push database schema (similar to setup_and_run.py)
        echo "📦 Pushing database schema..."
        if prisma db push; then
            echo "✅ Database schema pushed successfully"
        else
            echo "⚠️  Database schema push failed, continuing anyway..."
        fi
        
        # Create database views
        echo "🔨 Creating database views if missing..."
        if cd /app && python db_scripts/create_views.py; then
            echo "✅ Database views created successfully"
        else
            echo "⚠️  Failed to create database views, continuing with startup..."
        fi
    else
        echo "DATABASE_URL not set, skipping database initialization"
    fi
}

# Initialize database before starting the application
initialize_database

echo "🚀 Starting LiteLLM service..."

if [ "$USE_DDTRACE" = "true" ]; then
    export DD_TRACE_OPENAI_ENABLED="False"
    exec ddtrace-run litellm "$@"
else
    exec litellm "$@"
fi