#!/bin/bash

# Name: update_ollama.sh
# Description: Applies a systemd environment override to the ollama service, 
#              reloads systemd, and restarts the service.

# Exit immediately if any command fails
set -e

SERVICE_NAME="ollama"
OVERRIDE_FILE="/etc/systemd/system/${SERVICE_NAME}.service.d/override.conf"
ENVIRONMENT_CONFIG=""

echo "=============================================="
echo "🛡️ Starting configuration update for $SERVICE_NAME..."
echo "=============================================="

# --- Configuration Snippet to Apply ---
ENVIRONMENT_CONFIG="[Service]
Environment=\"OLLAMA_HOST=0.0.0.0:11434\"
"
# -------------------------------------


# 1. Write the override configuration file
echo "✅ Step 1/3: Writing service environment override to $OVERRIDE_FILE"

# Use 'tee' with sudo rights to write the content directly into the systemd directory structure
if echo "$ENVIRONMENT_CONFIG" | sudo tee "$OVERRIDE_FILE" > /dev/null; then
    echo "   Successfully created/updated the service override."
else
    echo "❌ ERROR: Failed to write the configuration file. Check permissions."
    exit 1
fi

# 2. Reload systemd daemon
echo -e "\n✅ Step 2/3: Running 'systemctl daemon-reload'..."
sudo systemctl daemon-reload

# 3. Restart the service
echo -e "\n✅ Step 3/3: Running 'systemctl restart $SERVICE_NAME'..."
sudo systemctl restart "$SERVICE_NAME"

echo "=============================================="
if sudo systemctl is-active "$SERVICE_NAME"; then
    echo "✨ SUCCESS! The $SERVICE_NAME service has been updated and successfully restarted."
else
    echo "❌ FAILURE: Could not verify the status of the $SERVICE_NAME service. Please check logs manually."
fi

echo "=============================================="