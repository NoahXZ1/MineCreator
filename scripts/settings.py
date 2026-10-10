"""Local settings; Windows protects saved API keys for the current user."""
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from dotenv import dotenv_values


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f'.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('Saving API keys requires Windows user protection.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    data = base64.b64decode(value, validate=True) if decrypt else value.encode('utf-8')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('Windows could not unlock or save the API key. Re-enter it in Settings.')
    try:
        output = ctypes.string_at(result.data, result.size)
        return output.decode('utf-8') if decrypt else base64.b64encode(output).decode('ascii')
    finally:
        kernel = ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def stored(root):
    path = root / 'data' / 'settings.json'
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or any(k in value and not isinstance(value[k],str) for k in ('key','model','game_dir')) or ('key_disabled' in value and type(value['key_disabled']) is not bool):
            raise ValueError('Invalid settings data.')
        return value
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise ValueError('Local settings could not be read. Re-enter your settings and API key, or choose Remove saved key.') from None


def effective(root):
    saved = stored(root)
    env = dotenv_values(root / '.env', encoding='utf-8-sig', interpolate=False)
    key = (protect(saved['key'], True) if saved.get('key') else '' if saved.get('key_disabled') else
           env.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY', ''))
    return dict(key=key.strip(), model=saved.get('model', env.get('OPENAI_MODEL') or os.environ.get('OPENAI_MODEL','gpt-6-luna')).strip(),
                game_dir=saved.get('game_dir', str(Path(os.environ.get('APPDATA', Path.home())) / '.minecraft')))


def public(root):
    value = effective(root)
    configured = bool(value['key'] and value['key'] not in ('your_api_key_here','你的完整密钥'))
    return dict(configured=configured, model=value['model'], game_dir=value['game_dir'], data_dir=str(root),
                message='API key configured locally.' if configured else 'Add an API key in Settings.')


def save(root, payload):
    model = payload.get('model','')
    if not isinstance(model,str):raise ValueError('Enter a valid model ID.')
    model=model.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,99}', model):
        raise ValueError('Enter a valid model ID.')
    if not isinstance(payload.get('game_dir'),str):raise ValueError('Select an existing Minecraft game directory.')
    directory = Path(payload.get('game_dir','')).expanduser()
    if not str(payload.get('game_dir','')).strip() or not directory.is_absolute() or not directory.is_dir():
        raise ValueError('Select an existing Minecraft game directory.')
    key = payload.get('api_key','')
    if not isinstance(key, str) or len(key)>1024 or (key.strip() and not re.fullmatch(r'[A-Za-z0-9_-]+',key.strip())):
        raise ValueError('Enter the API key without spaces or line breaks.')
    try:value=stored(root)
    except ValueError:
        if not key.strip() and payload.get('clear_key') is not True:raise
        value={}
    value.update(model=model, game_dir=str(directory.resolve()))
    if payload.get('clear_key') is True:
        value.pop('key',None)
        value['key_disabled'] = True
    elif key.strip():
        value.update(key=protect(key.strip()), key_disabled=False)
    atomic_json(root / 'data' / 'settings.json', value)
    return public(root)
