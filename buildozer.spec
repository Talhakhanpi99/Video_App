[app]
title = Video App Studio
package.name = videoappstudio
package.domain = com.promptpacks
source.dir = .
source.include_exts = py,kv,png,jpg,jpeg,txt,html,css,js,json,db,sqlite,webp,md
source.exclude_dirs = .buildozer,bin
version = 0.1.0
icon.filename = static/img/icon.png
requirements = python3,flask,werkzeug==2.0.3,requests,pillow,sqlite3,edge-tts
orientation = portrait
fullscreen = 1
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.enable_androidx = True
p4a.bootstrap = webview
p4a.port = 5000
log_level = 2
warn_on_root = 0

[buildozer]
log_level = 2
