@echo off
title Installation du Câble Virtuel (Sortie Audio Dédiée)
echo ======================================================================
echo    Installation du périphérique audio virtuel pour AirPlay Windows
echo ======================================================================
echo.
echo 1. Une fenêtre Windows (UAC) va s'ouvrir pour demander les droits admin.
echo    Cliquez sur "Oui".
echo.
echo 2. Dans la fenêtre VB-CABLE qui s'affiche, cliquez sur :
echo    [ Install Driver ]
echo.
echo 3. Une fois l'installation terminée, fermez la fenêtre.
echo ======================================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process '%~dp0tools\vbcable\VBCABLE_Setup_x64.exe' -Verb RunAs"
