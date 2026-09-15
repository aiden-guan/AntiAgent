#!/usr/bin/env bash
set -e

echo "🛡️ Installing AntiAgent for macOS..."

APP_DIR="$HOME/Applications"
mkdir -p "$APP_DIR"

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

echo "⬇️ Downloading latest AntiAgent release..."
curl -sL "https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent.zip" -o "$TMP_DIR/AntiAgent.zip"

echo "📦 Extracting application..."
ditto -x -k "$TMP_DIR/AntiAgent.zip" "$APP_DIR/"

echo "🔏 Clearing quarantine attributes..."
xattr -cr "$APP_DIR/AntiAgent.app" 2>/dev/null || true

echo "✅ AntiAgent.app installed successfully to $APP_DIR/AntiAgent.app!"
echo "🚀 Launching AntiAgent..."
open "$APP_DIR/AntiAgent.app"
