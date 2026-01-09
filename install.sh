#!/bin/bash
# AI SRE Agent Installation Script
# Run as: sudo ./install.sh

set -e

INSTALL_DIR="/home/melvin/server/ai-sre-agent"
SERVICE_USER="melvin"

echo "Installing AI SRE Agent..."

# Check if running as root for systemd installation
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo for systemd service installation"
    exit 1
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt" --quiet

# Create log file
touch /var/log/ai-sre-agent.log
chown "$SERVICE_USER:$SERVICE_USER" /var/log/ai-sre-agent.log

# Create systemd service
echo "Creating systemd service..."
cat > /etc/systemd/system/ai-sre-agent.service << 'EOF'
[Unit]
Description=AI SRE Agent - Plan-First Server Monitoring
Documentation=https://github.com/user/ai-sre-agent
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=melvin
Group=melvin
WorkingDirectory=/home/melvin/server/ai-sre-agent
ExecStart=/usr/bin/python3 /home/melvin/server/ai-sre-agent/agent.py daemon
Restart=always
RestartSec=30

# Environment
EnvironmentFile=-/home/melvin/server/ai-sre-agent/.env

# Security
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/melvin/server/ai-sre-agent/data
ReadWritePaths=/var/log/ai-sre-agent.log

# Resource limits
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7

[Install]
WantedBy=multi-user.target
EOF

# Create timer for periodic runs (alternative to daemon mode)
cat > /etc/systemd/system/ai-sre-agent.timer << 'EOF'
[Unit]
Description=AI SRE Agent Timer
Documentation=https://github.com/user/ai-sre-agent

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

# Create oneshot service for timer
cat > /etc/systemd/system/ai-sre-agent-check.service << 'EOF'
[Unit]
Description=AI SRE Agent - Single Check
After=network.target docker.service

[Service]
Type=oneshot
User=melvin
Group=melvin
WorkingDirectory=/home/melvin/server/ai-sre-agent
ExecStart=/usr/bin/python3 /home/melvin/server/ai-sre-agent/agent.py run
EnvironmentFile=-/home/melvin/server/ai-sre-agent/.env
EOF

# Reload systemd
systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Copy .env.example to .env and configure HA_TOKEN"
echo "   cp $INSTALL_DIR/.env.example $INSTALL_DIR/.env"
echo ""
echo "2. Test in dry-run mode:"
echo "   cd $INSTALL_DIR && python3 agent.py run --dry-run"
echo ""
echo "3. Enable the service (choose one):"
echo "   Option A - Daemon mode (recommended):"
echo "     sudo systemctl enable --now ai-sre-agent.service"
echo ""
echo "   Option B - Timer mode (runs every 5 minutes):"
echo "     sudo systemctl enable --now ai-sre-agent.timer"
echo ""
echo "4. Check status:"
echo "   systemctl status ai-sre-agent"
echo "   journalctl -u ai-sre-agent -f"
echo ""
echo "5. Manage plans:"
echo "   python3 agent.py list"
echo "   python3 agent.py approve <plan_id>"
echo "   python3 agent.py reject <plan_id>"
