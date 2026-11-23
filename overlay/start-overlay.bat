@echo off
rem Starts the errata stream overlay server on port 8090 (8080 belongs to GABS).
rem Double-click me, or let an errata session run me. Safe to run twice: the
rem second copy just fails to bind and exits.
cd /d "C:\Home\Fable 5\made\stream-overlay"
start "errata overlay" /min python server.py --port 8090
