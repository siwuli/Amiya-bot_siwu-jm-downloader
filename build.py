# -*- coding: UTF-8 -*-
"""
打包脚本：把 siwu-jm-downloader 插件及其依赖（jmcomic、pyzipper、Cryptodome）一起打包为 zip。

用法（在项目根目录下执行）：
    python pluginsServer/siwu-jm-downloader-1_0/build.py
"""

import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ZIP = os.path.join(ROOT, 'plugins', 'siwu-jm-downloader-1.0.zip')


def _site_package_candidates():
    candidates = [
        os.path.join(ROOT, 'venv', 'Lib', 'site-packages'),
        os.path.join(ROOT, '.venv', 'Lib', 'site-packages'),
    ]
    candidates.extend(sys.path)
    return candidates


def _detect_package_dir(package_name: str) -> str:
    """优先用 venv/site-packages 里的包；找不到则从系统 Python 中查找。"""
    for sp in _site_package_candidates():
        if not sp:
            continue
        cand = os.path.join(sp, package_name, '__init__.py')
        if os.path.isfile(cand):
            return os.path.dirname(cand)

    raise FileNotFoundError(
        f'未找到 {package_name} 包。请先执行 pip install {package_name}，然后再运行本脚本。'
    )


def _add_package(zf: zipfile.ZipFile, package_dir: str, package_name: str):
    for root, dirs, files in os.walk(package_dir):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        rel_root = os.path.relpath(root, package_dir)
        for f in files:
            if f.endswith(('.pyc', '.pyo')):
                continue
            full = os.path.join(root, f)
            arcname = os.path.normpath(os.path.join(package_name, rel_root, f)).replace('\\', '/')
            print(f'  + {package_name}: {arcname}')
            zf.write(full, arcname=arcname)


def build() -> str:
    packages = ['jmcomic', 'pyzipper', 'Cryptodome']
    package_dirs = {}
    for name in packages:
        package_dirs[name] = _detect_package_dir(name)
        print(f'{name} package dir: {package_dirs[name]}')

    os.makedirs(os.path.dirname(OUTPUT_ZIP), exist_ok=True)

    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in os.listdir(PLUGIN_DIR):
            if f == os.path.basename(__file__) or f.startswith('__pycache__'):
                continue
            full = os.path.join(PLUGIN_DIR, f)
            if os.path.isfile(full):
                arcname = os.path.basename(full)
                print(f'  + plugin: {arcname}')
                zf.write(full, arcname=arcname)

        for name, path in package_dirs.items():
            _add_package(zf, path, name)

    print(f'\ncreated: {OUTPUT_ZIP} ({os.path.getsize(OUTPUT_ZIP)} bytes)')
    return OUTPUT_ZIP


if __name__ == '__main__':
    build()
