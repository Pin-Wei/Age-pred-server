#!/bin/bash

SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_NAME="pinwei.service"

# mkdir -p "$SERVICE_DIR"

if [ ! -f $SERVICE_DIR/$SERVICE_NAME ]; then
	cat << EOF > $SERVICE_DIR/$SERVICE_NAME
[Unit]
Description=Auto Start Pinwei Tmux Session
After=network.target

[Service]
Type=forking

ExecStart=/bin/bash /media/data2/pinwei/Age_pred_server/server/start_service.sh
ExecStop=/usr/bin/tmux kill-session -t pinwei
Restart=on-failure

[Install]
WantedBy=default.target
EOF
	echo "Service file created at $SERVICE_DIR/$SERVICE_NAME"
fi

systemctl --user daemon-reload
systemctl --user enable $SERVICE_NAME
systemctl --user start $SERVICE_NAME

echo "Setup complete! You can check status with: systemctl --user status pinwei.service"