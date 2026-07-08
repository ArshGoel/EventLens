#!/bin/bash

# Exit on error
set -o errexit

echo "==> Running Database Migrations..."
python3 manage.py migrate --noinput

echo "==> Build complete!"
