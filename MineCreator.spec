# Build with: .venv/Scripts/python.exe -m PyInstaller MineCreator.spec --noconfirm
from pathlib import Path
root = Path(SPECPATH)
a = Analysis([str(root/'desktop.py')], pathex=[str(root)],
             binaries=[], datas=[(str(root/'ui'),'ui')],
             hiddenimports=['tkinter','tkinter.filedialog'],
             hookspath=[], runtime_hooks=[], excludes=['pytest','unittest'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='MineCreator',debug=False,
          bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='MineCreator')
