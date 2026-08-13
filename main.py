import os
import re
import sys
import json
import shutil
import asyncio

import aiohttp

from typing import Optional

from core import AmiyaBotPluginInstance, Chain, Message, log
from core.util import create_dir

curr_dir = os.path.dirname(__file__)
# Amiya-Bot 以包形式加载插件时，sys.path 只含 plugins 父目录，
# 需要把插件目录本身加进来，才能 import 自带的 jmcomic / Cryptodome / pyzipper
if curr_dir not in sys.path:
    sys.path.insert(0, curr_dir)
download_root = os.path.abspath(os.path.join(curr_dir, '..', '..', 'download', 'jmcomic'))

# 文件发送失败自动重试：最多尝试次数、重试间隔（秒）。
# 102902 为 QQ Highway 服务端瞬时拒绝（风控/网络波动），间隔拉长到 30 秒，
# 避免在风控窗口内连续撞墙。
FILE_SEND_MAX_ATTEMPTS = 3
FILE_SEND_RETRY_DELAY = 30
# OneBot v11 直连发送文件时的 HTTP 总超时（秒）。
# 框架默认走 aiohttp 的 300 秒总超时，大文件上传 Highway 耗时容易超时被掐断，这里放宽到 10 分钟
FILE_SEND_TIMEOUT = 600

bot = AmiyaBotPluginInstance(
    name='JM漫画下载',
    version='1.1.5',
    plugin_id='siwu-jm-downloader',
    plugin_type='functional',
    description='通过输入 JM 编号（如 350234）下载禁漫本子，并打包为压缩包发送',
    document=f'{curr_dir}/README.md',
    global_config_default=f'{curr_dir}/config_default.yaml',
    global_config_schema=f'{curr_dir}/jsonSchema.json',
)


def _cfg(key: str, default=None):
    """从控制台读全局配置。"""
    val = bot.get_config(key, channel_id=None)
    return default if val is None else val


def resolve_zip_password(album_id: str) -> Optional[str]:
    """开启加密时返回密码；关闭则返回 None。自定义密码留空时用本子编号。"""
    if not bool(_cfg('jm_zip_password_enabled', False)):
        return None
    custom = str(_cfg('jm_zip_password', '') or '').strip()
    return custom or str(album_id)


def extract_album_id(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(r'找本[\s ]*?(\d+)', text)
    if match:
        return match.group(1)
    return None


async def download_and_pack(album_id: str, status_cb=None, zip_password: Optional[str] = None) -> dict:
    """下载指定编号的本子并打包为 zip，返回 {'zip_path', 'title', 'size', 'password'}"""

    import jmcomic

    create_dir(download_root)

    if status_cb:
        await status_cb(f'博士，兔兔正在解析 JM{album_id}…')

    title = ''
    try:
        client = jmcomic.JmOption.default().new_jm_client()
        album = client.get_album_detail(album_id)
        title = album.name if album and album.name else ''
    except Exception as e:
        log.warning(f'获取 JM{album_id} 元信息失败: {e}')

    if status_cb:
        tip = f'博士，兔兔正在下载 JM{album_id} 《{title or "未知标题"}》，请耐心等待…'
        if zip_password:
            tip += '（完成后将加密打包）'
        await status_cb(tip)

    loop = asyncio.get_running_loop()

    # 文件名只用本子编号，避免标题过长导致部分适配器发不出去
    zip_kwargs = {
        'zip_dir': download_root,
        'delete_original_file': True,
        'filename_rule': 'Aid',
    }
    if zip_password:
        zip_kwargs['encrypt'] = {'password': zip_password}

    def _download():
        jmcomic.download_album(
            album_id,
            extra=jmcomic.Feature.export_zip(**zip_kwargs),
        )

    await loop.run_in_executor(None, _download)

    # 优先使用编号命名的 zip；兼容旧产物再做模糊匹配
    zip_path = os.path.join(download_root, f'{album_id}.zip')
    if not os.path.exists(zip_path) and os.path.isdir(download_root):
        candidates = []
        for f in os.listdir(download_root):
            if not f.lower().endswith('.zip'):
                continue
            if f == f'{album_id}.zip' or f'JM{album_id}' in f or f.startswith(f'{album_id}'):
                candidates.append(os.path.join(download_root, f))
        if candidates:
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            zip_path = candidates[0]

    if not zip_path or not os.path.exists(zip_path):
        raise FileNotFoundError(f'未找到 JM{album_id} 的 zip 产物（可能被 JM 拒绝）')

    size = os.path.getsize(zip_path)
    return {
        'zip_path': zip_path,
        'title': title or f'JM{album_id}',
        'size': size,
        'password': zip_password,
    }


# ---------- 不同 adapter 的文件发送 ----------

def _adapter_kind(data: Message) -> str:
    instance = data.instance
    cls_name = type(instance).__name__
    module = type(instance).__module__ or ''
    if 'qqGroup' in module or cls_name == 'QQGroupBotInstance':
        return 'qq_group'
    if 'onebot.v11' in module or cls_name == 'OneBot11Instance':
        return 'onebot_v11'
    if 'onebot.v12' in module or cls_name == 'OneBot12Instance':
        return 'onebot_v12'
    if 'mirai' in module or cls_name == 'MiraiBotInstance':
        return 'mirai'
    if 'qqGuild' in module or cls_name == 'QQGuildBotInstance':
        return 'qq_guild'
    if 'comwechat' in module or cls_name == 'ComWeChatBotInstance':
        return 'comwechat'
    return cls_name


def _local_http_url_for(file_path: str, data: Message) -> str:
    """把本地文件拷贝到 bot 临时目录并返回 http URL（QQ Group 适配器需要 http URL）"""
    resource_root = './resource/group_temp'
    create_dir(resource_root)
    file_name = os.path.basename(file_path)
    temp_path = os.path.join(resource_root, file_name)
    if os.path.abspath(file_path) != os.path.abspath(temp_path):
        shutil.copy(file_path, temp_path)

    port = 8086
    if hasattr(data.instance, 'http_port') and data.instance.http_port:
        port = data.instance.http_port

    return f'http://127.0.0.1:{port}/resource/group_temp/{file_name}'


async def send_zip_file(data: Message, zip_path: str):
    kind = _adapter_kind(data)
    title = os.path.basename(zip_path)
    size_mb = os.path.getsize(zip_path) / 1024 / 1024

    try:
        if kind == 'qq_group':
            return await send_via_qq_group(data, zip_path)
        if kind == 'onebot_v11':
            return await send_via_onebot_v11(data, zip_path)
        if kind == 'onebot_v12':
            return await send_via_onebot_v12(data, zip_path)
        if kind == 'mirai':
            return send_via_mirai(data, zip_path)
        if kind == 'qq_guild':
            return await send_via_qq_guild(data, zip_path, title)
        if kind == 'comwechat':
            return send_via_comwechat(data, zip_path)
    except Exception as e:
        log.error(f'发送文件时出错了: {e}')
        return Chain(data).text(
            f'博士，发送文件时出错了：{e}\n文件已保存到：{zip_path}'
        )

    return Chain(data).text(
        f'博士，兔兔已下载完成（{size_mb:.2f} MB），但当前 bot 适配器（{kind}）暂不支持直接发送文件。\n'
        f'文件保存在服务器：{zip_path}'
    )


def send_result_error(result) -> Optional[str]:
    """检查 Message.send 的返回值，返回发送失败原因；发送成功则返回 None。"""
    if not result:
        return None

    callbacks = result if isinstance(result, list) else [result]
    for callback in callbacks:
        response = getattr(callback, 'response', None)
        if response is None:
            continue

        # 网络层错误
        if getattr(response, 'error', None):
            return f'网络错误：{response.error}'

        body = getattr(response, 'json', None)
        if not isinstance(body, dict):
            continue

        status = str(body.get('status', '')).lower()
        retcode = body.get('retcode')
        code = body.get('code')

        if status in ('failed', 'error'):
            failed = True
        elif retcode not in (None, 0) or code not in (None, 0):
            # 非 0 的 retcode/code；状态为 async 时说明已入队，不视为失败
            failed = status != 'async'
        else:
            failed = False

        if failed:
            msg = body.get('message') or body.get('msg') or body.get('wording') or ''
            return str(msg) or f'status={status} retcode={retcode} code={code}'

    return None


def friendly_send_error(reason: str) -> str:
    """把底层发送错误翻译成用户能看懂的中文提示。"""
    r = reason or ''
    low = r.lower()
    if '102902' in r:
        return 'QQ 上传通道失败（102902），多为网络波动，请稍后重试'
    if 'highway' in low:
        return 'QQ 上传通道异常（多为网络波动），请稍后重试'
    if 'timed out' in low or 'timeout' in low:
        return '发送超时（网络延迟过高），请稍后重试'
    if 'too large' in low or '文件过大' in r:
        return '文件过大，超出 QQ 上传限制'
    if 'not exist' in low or '不存在' in r:
        return '文件不存在或已被移动'
    return r


async def send_file_with_retry(data: Message, file_chain: Chain, album_id: str) -> Optional[str]:
    """发送文件，失败自动重试；全部失败时返回用户可读的失败原因，成功返回 None。"""
    reason = None
    for attempt in range(1, FILE_SEND_MAX_ATTEMPTS + 1):
        try:
            reason = send_result_error(await data.send(file_chain))
        except Exception as e:
            log.error(f'发送 JM{album_id} 文件消息失败: {e}')
            reason = str(e)

        if not reason:
            return None

        log.warning(f'发送 JM{album_id} 文件失败（第 {attempt}/{FILE_SEND_MAX_ATTEMPTS} 次）: {reason}')
        if attempt < FILE_SEND_MAX_ATTEMPTS:
            await asyncio.sleep(FILE_SEND_RETRY_DELAY)

    return friendly_send_error(reason)


async def send_via_qq_group(data: Message, zip_path: str):
    """QQ 群通道：上传文件获得 file_info，再用富媒体消息发送"""
    api = getattr(data.instance, 'api', None)
    if not api or not hasattr(api, 'upload_file'):
        return Chain(data).text(f'博士，未找到 QQ 群上传文件接口。\n文件保存在：{zip_path}')

    url = _local_http_url_for(zip_path, data)
    openid = data.user_openid if data.is_direct else data.channel_openid

    res = None
    try:
        res = await api.upload_file(openid, 4, url, is_direct=data.is_direct)
    except Exception as e:
        log.error(f'QQ 群上传文件失败: {e}')

    if not res:
        return Chain(data).text(f'博士，QQ 群上传文件失败。\n文件保存在：{zip_path}')

    file_info = None
    if isinstance(res, dict):
        if 'file_info' in res:
            file_info = res['file_info']
        elif 'data' in res and isinstance(res['data'], dict) and 'file_info' in res['data']:
            file_info = res['data']['file_info']

    if not file_info:
        return Chain(data).text(f'博士，上传结果异常：{res}\n文件保存在：{zip_path}')

    payload = {
        'msg_type': 7,
        'media': {'file_info': file_info},
        'msg_id': data.message_id,
        'msg_seq': 1,
    }

    try:
        if data.is_direct:
            res = await api.post_private_message(data.user_openid, payload)
        else:
            res = await api.post_group_message(data.channel_openid, payload)
    except Exception as e:
        log.error(f'QQ 群发送文件失败: {e}')
        return Chain(data).text(f'博士，QQ 群发送文件失败：{e}')

    # 检查发送结果，失败时给出反馈
    if res is not None:
        if getattr(res, 'error', None):
            log.error(f'QQ 群发送文件失败: {res.error}')
            return Chain(data).text(f'博士，QQ 群发送文件失败：{res.error}')
        body = getattr(res, 'json', None) if hasattr(res, 'json') else res
        if isinstance(body, dict) and body.get('code', 0) != 0:
            msg = body.get('message') or body.get('msg') or str(body)
            log.error(f'QQ 群发送文件失败: {body}')
            return Chain(data).text(f'博士，QQ 群发送文件失败：{msg}')

    return None


async def send_via_onebot_v11(data: Message, zip_path: str) -> Optional[Chain]:
    """OneBot v11 通道：直连 OneBot HTTP 接口发送文件段，失败自动重试。

    不走框架的 data.send（其 HTTP 请求有默认 5 分钟总超时），改为直连并放宽到
    FILE_SEND_TIMEOUT 秒，避免大文件上传耗时过长被超时掐断。
    """
    instance = data.instance
    host = getattr(instance, 'host', None)
    http_port = getattr(instance, 'http_port', None)
    token = getattr(instance, 'token', None)
    if not host or not http_port:
        return Chain(data).text(f'博士，未找到 OneBot v11 接口地址。\n文件保存在：{zip_path}')

    message = []
    if not data.is_direct and data.user_id:
        message.append({'type': 'at', 'data': {'qq': data.user_id}})
    message.append(
        {
            'type': 'file',
            'data': {
                'file': 'file:///' + os.path.abspath(zip_path).replace('\\', '/'),
                'name': os.path.basename(zip_path),
            },
        }
    )
    payload = {
        'message_type': 'private' if data.is_direct else 'group',
        'user_id': data.user_id,
        'group_id': data.channel_id,
        'message': message,
    }
    payload = {k: v for k, v in payload.items() if v is not None and v != ''}

    url = f'http://{host}:{http_port}/send_msg'
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = token

    timeout = aiohttp.ClientTimeout(total=FILE_SEND_TIMEOUT)

    reason = None
    for attempt in range(1, FILE_SEND_MAX_ATTEMPTS + 1):
        reason = None
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as res:
                    text = await res.text()
                    status = res.status

            if status >= 400:
                reason = f'OneBot HTTP {status}: {text[:200]}'
            else:
                try:
                    body = json.loads(text) if text else None
                except json.JSONDecodeError:
                    body = None
                if isinstance(body, dict):
                    if body.get('status') not in (None, 'ok', 'async') or body.get('retcode') not in (None, 0):
                        reason = body.get('message') or body.get('msg') or str(body)
        except Exception as e:
            log.error(f'OneBot v11 发送文件请求异常: {e}')
            reason = str(e)

        if not reason:
            return None

        log.warning(f'OneBot v11 发送文件失败（第 {attempt}/{FILE_SEND_MAX_ATTEMPTS} 次）: {reason}')
        if attempt < FILE_SEND_MAX_ATTEMPTS:
            await asyncio.sleep(FILE_SEND_RETRY_DELAY)

    return Chain(data).text(
        f'博士，OneBot 发送文件失败了：{friendly_send_error(reason)}\n文件保存在：{zip_path}'
    )


async def send_via_onebot_v12(data: Message, zip_path: str):
    api = getattr(data.instance, 'api', None)
    file_id = None
    if api and hasattr(api, 'post'):
        try:
            res = await api.post(
                '/',
                {
                    'action': 'upload_file',
                    'params': {
                        'type': 'path',
                        'name': os.path.basename(zip_path),
                        'path': os.path.abspath(zip_path),
                    },
                },
            )
            if res and 'data' in res.json and 'file_id' in res.json['data']:
                file_id = res.json['data']['file_id']
        except Exception as e:
            log.warning(f'OneBot v12 upload_file 失败: {e}')

    if file_id:
        return Chain(data).extend({'type': 'file', 'data': {'file_id': file_id}})

    return Chain(data).extend(
        {
            'type': 'file',
            'data': {
                'uri': 'file:///' + os.path.abspath(zip_path).replace('\\', '/'),
                'name': os.path.basename(zip_path),
            },
        }
    )


def send_via_mirai(data: Message, zip_path: str):
    return Chain(data).extend(
        {
            'type': 'File',
            'path': os.path.abspath(zip_path),
            'name': os.path.basename(zip_path),
        }
    )


async def send_via_qq_guild(data: Message, zip_path: str, title: str):
    """QQ 频道通道：先发到本地 http 临时目录，再用图片/附件方式发送"""
    url = _local_http_url_for(zip_path, data)
    return Chain(data).extend(
        {
            'type': 'file',
            'data': {'url': url, 'name': title},
        }
    )


def send_via_comwechat(data: Message, zip_path: str):
    return Chain(data).extend(
        {
            'type': 'file',
            'data': {'file': os.path.abspath(zip_path), 'name': os.path.basename(zip_path)},
        }
    )


# ---------- 主命令 ----------

@bot.on_message(keywords=['找本'], allow_direct=True, level=5)
async def _(data: Message):
    if not bool(_cfg('jm_enabled', True)):
        return None

    album_id = extract_album_id(data.text)
    if not album_id:
        return Chain(data).text('博士，请告诉兔兔要找的本子编号，例如：\n兔兔找本 350234')

    zip_password = resolve_zip_password(album_id)

    async def status(msg):
        try:
            await data.send(Chain(data, at=False).text(msg))
        except Exception:
            pass

    await status(f'博士，兔兔收到任务：JM{album_id}，开始排队下载～')

    try:
        result = await download_and_pack(album_id, status_cb=status, zip_password=zip_password)
    except Exception as e:
        log.error(f'下载 JM{album_id} 失败: {e}')
        return Chain(data).text(f'博士，下载 JM{album_id} 失败了：{e}')

    zip_path = result['zip_path']
    title = result['title']
    size_mb = result['size'] / 1024 / 1024
    password = result.get('password')

    done_text = f'博士，《{title}》下载完成（{size_mb:.2f} MB），压缩包见下方：'
    if password:
        done_text += f'\n压缩包密码：{password}'

    await data.send(Chain(data).text(done_text))
    file_chain = await send_zip_file(data, zip_path)
    if file_chain:
        fail_reason = await send_file_with_retry(data, file_chain, album_id)
        if fail_reason:
            log.warning(f'发送 JM{album_id} 文件最终失败: {fail_reason}')
            await data.send(
                Chain(data).text(
                    f'博士，文件发送失败了：{fail_reason}\n压缩包已保存在服务器：{zip_path}'
                )
            )
    return None