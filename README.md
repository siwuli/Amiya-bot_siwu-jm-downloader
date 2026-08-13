# 兔兔 - AmiyaBot JM 漫画下载插件

通过输入 JM 禁漫本子编号，让兔兔自动下载并打包为压缩包发送给你。

> 插件 id：`siwu-jm-downloader`　当前版本：`1.1.3`

## 触发方式

```
兔兔找本 [编号]
```

- 例：`兔兔找本 350234`
- 例：`兔兔找本350234`（中间无空格也可以）

## 功能说明

1. 兔兔解析消息中的数字编号
2. 调用 `jmcomic` 库下载对应本子所有图片
3. 下载完成后自动打包为 zip（**文件名仅为本子编号**，如 `350234.zip`，避免标题过长导致发送失败）
4. 根据当前 bot 适配器，将压缩包作为文件发送给用户
5. 下载文件保存在 `download/jmcomic/` 目录
6. 可在控制台配置是否给压缩包加密；开启且未自定义密码时，默认用本子编号作密码

## 控制台配置

安装后可在兔兔控制台修改（对应 `config_default.yaml` / `jsonSchema.json`）：

| 配置项 | 说明 | 默认 |
| --- | --- | --- |
| `jm_enabled` | 插件总开关 | `true` |
| `jm_zip_password_enabled` | 是否给导出 zip 添加密码 | `false` |
| `jm_zip_password` | 自定义密码；留空且开启加密时，使用当前本子编号 | 空 |

开启加密后，下载完成的提示里会附带密码。

## 支持的适配器

| 适配器 | 文件发送方式 |
| --- | --- |
| QQ 群聊（`qq_group`） | 通过 `upload_file` 接口 + 富媒体消息（`msg_type=7`）发送 |
| OneBot v11 | 通过 CQ 码 `file://` 段发送 |
| OneBot v12 | 通过 `upload_file` action 或 file 段发送 |
| mirai | 通过 `File` 消息段发送 |
| QQ 频道 | 拷贝到本地 http 临时目录发送（频道 SDK 对文件支持有限） |
| 企业微信 | 通过 file 段发送 |

## 依赖

- Python 包：`jmcomic`、`pyzipper`、`Cryptodome`（**已随插件 zip 一起打包**，运行时无需手动安装；后两者用于压缩包加密）
- 第三方依赖（`commonx`, `curl-cffi`, `pyyaml`, `pycryptodome`, `pillow`）通常 PyInstaller 打包兔兔时已经包含。如未包含，请 `pip install commonx curl-cffi pyyaml pycryptodome pillow`
- 首次使用前请确认你的网络环境能访问 JM 禁漫域名

## 安装方法

1. 把 `siwu-jm-downloader-<版本号>.zip`（例如 `siwu-jm-downloader-1.1.2.zip`）放到 `plugins/` 目录下（已为你打包好，含 jmcomic / pyzipper / Cryptodome）
2. 重启兔兔即可自动加载

### 修改后重新打包

```bash
# 在项目根目录运行，会自动从 venv/site-packages 里找到 jmcomic、pyzipper、Cryptodome 并打包
python pluginsServer/siwu-jm-downloader-1_0/build.py
```

## 版本记录

每次发版在表格最上方追加一行。

| 版本 | 更新内容 |
|---|---|
| `1.1.3` | 文件发送失败自动重试 3 次（间隔 5 秒）；失败提示改为友好中文文案 |
| `1.1.2` | 文件发送失败时给出反馈（检查发送结果，失败提示原因并告知压缩包保存路径） |
| `1.1.1` | 压缩包文件名改为本子编号（如 `350234.zip`），避免标题过长导致部分适配器发不出去 |
| `1.1` | 增加控制台配置（`config_default.yaml` / `jsonSchema.json`）；支持压缩包加密，自定义密码留空时默认使用本子编号；示例编号改为 `350234` |
| `1.0` | 初始版本，支持通过编号下载并打包发送 |

## 项目地址

<https://github.com/siwuli/Amiya-bot_siwu-jm-downloader>
