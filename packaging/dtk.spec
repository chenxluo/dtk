# -*- mode: python ; coding: utf-8 -*-
# One onedir, two executables: DTK.exe (desktop window) + dtk.exe (console CLI).
# Data ships separately (first-run bootstrap downloads it from GitHub Releases).

import os

repo_root = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

common = dict(
    pathex=[repo_root],
    binaries=[],
    hiddenimports=["webview.platforms.edgechromium", "webview.platforms.winforms"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

gui = Analysis(
    [os.path.join(repo_root, "packaging", "entry_gui.py")],
    datas=[(os.path.join(repo_root, "web"), "web")],
    **common,
)
cli = Analysis(
    [os.path.join(repo_root, "packaging", "entry_cli.py")],
    datas=[],
    **common,
)

pyz_gui = PYZ(gui.pure)
exe_gui = EXE(
    pyz_gui,
    gui.scripts,
    [],
    exclude_binaries=True,
    name="DTK",
    icon=os.path.join(repo_root, "packaging", "dtk.ico"),
    console=False,
)
pyz_cli = PYZ(cli.pure)
exe_cli = EXE(
    pyz_cli,
    cli.scripts,
    [],
    exclude_binaries=True,
    name="dtk-cli",
    icon=os.path.join(repo_root, "packaging", "dtk.ico"),
    console=True,
)

coll = COLLECT(exe_gui, exe_cli, gui.binaries, gui.datas, cli.binaries, name="DTK")
