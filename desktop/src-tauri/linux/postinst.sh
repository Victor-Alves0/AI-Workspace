#!/bin/sh
# O bundler do Tauri grava os `deb.files` como 0644: sem isto, `aiw` daria
# "permissão negada". O arquivo continua sendo do dpkg (sai na desinstalação).
chmod 0755 /usr/bin/aiw
