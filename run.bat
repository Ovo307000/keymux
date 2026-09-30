@echo off
rem Start KeyMux on the address from config.yaml (127.0.0.1:9000 by default).
setlocal
cd /d "%~dp0"

if not exist ".env" (
    echo [KeyMux] .env not found. Copy .env.example to .env and fill it in.
    exit /b 1
)

python -m keymux.main
