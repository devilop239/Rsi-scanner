#!/bin/bash
# Nifty 50 Scanner VPS Deployment Script

set -e

echo "🚀 Starting Nifty 50 Scanner VPS Deployment..."

# 1. Check if Docker and Docker Compose are installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh"
    exit 1
fi

# 2. Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠️  No .env file found!"
    if [ -f .env.example ]; then
        echo "   Creating .env from .env.example. PLEASE EDIT IT BEFORE CONTINUING."
        cp .env.example .env
        exit 1
    else
        echo "   Please create a .env file with your Telegram Bot Token and Cron Secret."
        exit 1
    fi
fi

# 3. Build and deploy
echo "📦 Building Docker images and spinning up containers..."
docker compose up --build -d

echo ""
echo "✅ Deployment Successful!"
echo ""
echo "📊 Database:    Running internally on port 5432 (nifty_db container)"
echo "🤖 Bot Server:  Running internally on port 8000 (nifty_bot container)"
echo "🕒 Cron Job:    Automated sidecar scheduled for 16:30 IST (nifty_cron container)"
echo ""
echo "To view live logs, run:"
echo "  docker compose logs -f"
