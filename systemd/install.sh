#!/bin/bash
# Install AI SRE Agent systemd services
# Usage: sudo ./install.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing AI SRE Agent systemd services..."

# Copy service files
cp "$SCRIPT_DIR/ai-sre-agent.service" /etc/systemd/system/
cp "$SCRIPT_DIR/signal-chat.service" /etc/systemd/system/

# Reload systemd
systemctl daemon-reload

echo "Services installed. To enable:"
echo "  sudo systemctl start signal-chat"
echo "  sudo systemctl start ai-sre-agent"
echo ""
echo "To enable on boot:"
echo "  sudo systemctl enable signal-chat"
echo "  sudo systemctl enable ai-sre-agent"
echo ""
echo "To view logs:"
echo "  journalctl -u signal-chat -f"
echo "  journalctl -u ai-sre-agent -f"
