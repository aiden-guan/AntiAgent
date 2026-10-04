#!/usr/bin/env bash
set -e

OS="$(uname -s)"

if [ "$OS" = "Darwin" ]; then
    echo "🛡️ Installing AntiAgent for macOS..."

    APP_DIR="$HOME/Applications"
    mkdir -p "$APP_DIR"

    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT

    echo "⬇️ Downloading latest AntiAgent release for macOS..."
    curl -sL "https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent.zip" -o "$TMP_DIR/AntiAgent.zip"

    echo "📦 Extracting application..."
    ditto -x -k "$TMP_DIR/AntiAgent.zip" "$APP_DIR/"

    echo "🔏 Clearing quarantine attributes..."
    xattr -cr "$APP_DIR/AntiAgent.app" 2>/dev/null || true

    echo "✅ AntiAgent.app installed successfully to $APP_DIR/AntiAgent.app!"
    echo "🚀 Launching AntiAgent..."
    open "$APP_DIR/AntiAgent.app"

elif [ "$OS" = "Linux" ]; then
    echo "🛡️ Installing AntiAgent for Linux..."

    INSTALL_DIR="$HOME/.local/share/antiagent"
    mkdir -p "$INSTALL_DIR"

    TMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TMP_DIR"' EXIT

    echo "⬇️ Downloading latest AntiAgent release for Linux..."
    curl -sL "https://github.com/aiden-guan/AntiAgent/releases/latest/download/AntiAgent-Linux.tar.gz" -o "$TMP_DIR/AntiAgent-Linux.tar.gz"

    echo "📦 Extracting application..."
    tar -xzf "$TMP_DIR/AntiAgent-Linux.tar.gz" -C "$TMP_DIR"

    # Copy files into ~/.local/share/antiagent
    cp -rf "$TMP_DIR/AntiAgent/"* "$INSTALL_DIR/"
    chmod +x "$INSTALL_DIR/AntiAgent.sh" "$INSTALL_DIR/install-app.sh" 2>/dev/null || true

    echo "⚙️  Configuring desktop application & safety hook..."
    "$INSTALL_DIR/install-app.sh"

    echo "🚀 Launching AntiAgent Guard..."
    "$INSTALL_DIR/AntiAgent.sh" app &
else
    echo "❌ Unsupported operating system: $OS"
    echo "Please visit https://github.com/aiden-guan/AntiAgent for installation instructions."
    exit 1
fi
