#!/bin/bash

# --- Configuration ---
BACKUP_DIR="/home/kstoltzfus/backups"     
VOLUME_HOST_PATH="/home/kstoltzfus/.local/share/containers/storage/volumes" 

# Database Configuration
CASH_CUB="cash_cub"
ODB_TRAK="obd_trak"
USERNAME="kstoltzfus"
HOST_PORT="localhost"

# --- Security Configuration ---
# Point PostgreSQL to the secure password file
export PGPASSFILE="/home/kstoltzfus/scripts/.pgpass"

# --- Setup and Pre-Flight Checks ---
set -euo pipefail # Exit immediately if a command fails

echo "--- Starting Full Backup Process ---"

# Ensure the backup directory exists
mkdir -p $BACKUP_DIR

# ==================================================
# 1. DATABASE BACKUP (SQL Dumps)
# ==================================================
echo -e "\n--- Stage 1: Backing up PostgreSQL Databases ---"

backup_db() {
    DB_NAME=$1
    echo "-> Backing up database: $DB_NAME..."
    
    pg_dump -h $HOST_PORT -U $USERNAME $DB_NAME | gzip > ${BACKUP_DIR}/${DB_NAME}_$(date +%Y%m%d%H%M%S).sql.gz
    echo "✅ Success: Database $DB_NAME saved."
}

backup_db $CASH_CUB
backup_db $ODB_TRAK

# ==================================================
# 2. VOLUME BACKUP (Podman Data)
# ==================================================
echo -e "\n--- Stage 2: Backing up Podman Volumes ---"

backup_volume() {
    VOLUME_NAME=$1
    CONTAINER_NAME=$2
    echo "Stopping container $CONTAINER_NAME to ensure data integrity..."
    podman stop $CONTAINER_NAME

    # Check if the volume path exists before proceeding
    if [ ! -d "$VOLUME_HOST_PATH" ]; then
        echo "❌ CRITICAL ERROR: Volume path $VOLUME_HOST_PATH does not exist or is incorrect. Aborting volume backup."
        exit 1
    fi

    VOLUME_BACKUP_FILE="${BACKUP_DIR}/${VOLUME_NAME}_$(date +%Y%m%d%H%M%S).tar.gz"

    echo "Creating archive for volume data at $VOLUME_HOST_PATH..."
    podman volume export $VOLUME_NAME | gzip > $VOLUME_BACKUP_FILE

    echo "✅ Success: Volume data saved to $VOLUME_BACKUP_FILE."

    echo "Restarting container $CONTAINER_NAME..."
    podman start $CONTAINER_NAME
}

backup_volume "lubelogger_data" "lubelogger"
backup_volume "lubelogger_keys" "lubelogger"
backup_volume "open-webui" "open-webui"
backup_volume "core-data" "searxng-core"
backup_volume "core-config" "searxng-core"
backup_volume "valkey-data" "searxng-valkey"
backup_volume "open-terminal" "open-terminal"

echo -e "\n=========================================="
echo "🚀 FULL BACKUP COMPLETED SUCCESSFULLY!"
echo "Files are saved in the $BACKUP_DIR directory."
echo "=========================================="