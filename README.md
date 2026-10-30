# Orbit · 桌面 Codex 助手

一个原生 Qt 桌宠。在 Ubuntu GNOME/X11 上可以直接安装；在 64 位 Windows 10 或更高版本上可以装进开始菜单，也可以打成安装包。左键点击可和本机 Codex 聊天，拖到窗口上可吸附，右键可设置工作目录和登录自启动。原来的浏览器交互原型仍保留，便于查看设计。

## 在 Linux 上安装

需要 Python 3、PyQt5、`xprop`、`xwininfo` 和已登录的 Codex CLI。本机当前环境已具备这些组件。运行：

```bash
./install.sh
~/.local/share/orbit-codex/orbit-codex
```

安装脚本会把 `orbit` 包和启动入口复制到 `~/.local/share/orbit-codex`，创建应用菜单项，并在 `~/.config/autostart` 创建登录自启动项。右键桌宠进入“设置”，可关闭自启动、选择回复来源或 Codex 工作目录。卸载运行 `./uninstall.sh`。

直接在项目目录运行：

```bash
python3 native_app.py
```

## 在 Windows 上安装

需要 64 位 Windows 10 或更高版本。窗口吸附使用系统窗口列表：文件资源管理器会读取当前文件夹，终端会读取其中 shell 的目录，Cursor 和 VS Code 会按窗口标题在最近的工作区里查找文件。浏览器通常只有窗口标题。

不打包、直接用本机 Python 安装时，先安装 64 位 Python 3.11 或更高版本并加入 PATH，再在项目目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1
```

脚本会安装 PyQt5，把程序复制到 `%LOCALAPPDATA%\Programs\Orbit`，并创建开始菜单快捷方式。加上 `-Autostart` 会同时写入登录启动项。右键吉祥物进入“设置”，也可以之后再开关自启动。卸载：

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\Orbit\uninstall-windows.ps1"
```

也可以不安装，在项目目录执行 `python native_app.py`。

要做成可以拷到其他电脑的安装包，在 Windows 上运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build-windows.ps1
```

脚本会用 PyInstaller 打包，并调用 Inno Setup 6 生成 `dist\Orbit-Setup.exe`。如果还没有 Inno Setup，脚本会尝试用 winget 安装。把这个安装包拷到其他 Windows 电脑上双击即可安装，对方不需要预先安装 Python。安装程序可以选择桌面快捷方式和登录后自动启动。安装目录里的 `config.toml` 在升级时不会被覆盖；访问令牌放在同目录的 `api.txt`，或 `%APPDATA%\orbit-codex\api.txt`。

这个仓库当前在 Linux 上，不能在这里直接编出 `.exe`。安装包需要在 Windows 上执行上面的脚本后才会出现。

## 配置

项目根目录的 `config.toml` 控制吉祥物大小、对话窗口初始尺寸、默认接口、颜色和快捷提问。例如把吉祥物放大到 1.4 倍：

```toml
[mascot]
scale = 1.4

[api]
provider = "api"
endpoint = "https://你的服务/v1/chat/completions"
model = "你的模型"
```

访问令牌仍放在 `api.txt`，不要写进配置。设置窗口保存过的目录和接口，在 Linux 上位于 `~/.config/orbit-codex/settings.json`，在 Windows 上位于 `%APPDATA%\orbit-codex\settings.json`，会覆盖配置文件里的同名默认值。修改 `config.toml` 后重新启动 Orbit。重新运行 `install.sh` 会更新程序，但不会覆盖已经安装过的那份 `config.toml`。

Codex 通过本机 `codex app-server` 运行，使用当前 Codex CLI 登录状态。每个工作目录会建立独立会话。Codex 在工作目录内使用 `workspace-write` 沙箱，额外权限请求会被拒绝。吸附后会读取目标窗口的路径和一份有限的内容：文件管理器提供当前目录和文件名，代码编辑器提供当前文件路径和开头文本，终端提供命令提示符里的目录和前台命令。浏览器通常只能提供窗口标题；地址只有在辅助功能接口暴露时才会附上。这些上下文会随消息发给 Codex，识别到的目录会作为本次会话的工作目录。密钥样式的文件名不会被读入提示。Linux 版需要 X11，会在 GNOME 登录后启动。Windows 版在登录后的桌面会话中运行。两边都不是系统开机后、登录前运行的后台服务。

## 浏览器设计原型

原型无需安装依赖，展示灵动岛式 AI 助手、弹力带和模拟窗口。

## 运行

在项目目录执行：

```bash
./open-prototype.sh
```

脚本会明确使用浏览器打开原型，避免本机错误的 HTML 默认打开程序。也可启动本地服务器：

```bash
python3 -m http.server 8080
```

打开 <http://localhost:8080/index.html>。不要在文件管理器中直接双击 HTML：当前系统把 HTML 默认关联到了 Grok Bot。

## 体验

- 从顶部或已连接的窗口拖动黑色吉祥物到文件管理器、代码编辑器或浏览器窗口。吉祥物和弹性连接带保持黑色；拉到约 220 像素后连接带断开，吉祥物仍可继续拖动。
- 连接带在短距离拖拽时也会逐渐收窄，中段像橡胶一样拉细并轻微流动；断开时由两端回缩并产生细小粒子。
- 拖到窗口上时，目标窗口会高亮，浮层会显示窗口名称和可用操作。
- 松开后黑色带耳朵的吉祥物会弹跳落在窗口上沿，两只小爪搭在标题栏上；窗口显示连接标记，对话框随后在外侧展开，显示对应的路径和上下文。
- 从已连接窗口直接拖到另一个窗口，可以立即切换；若松在桌面空白处，助手会断开并弹回桌面顶部。点击对话框的 × 也可断开连接。
- 输入消息或点快捷建议。文件管理器中的“整理文件”会改变原型中的文件排列。
- 吉祥物有待命、拖拽、思考、执行和完成五种表情与动作；发送演示消息后会依次显示思考、执行和完成状态。
- 待命时点击吉祥物可直接聊天。右键吉祥物或点击菜单栏的 Orbit 可打开设置、回到顶部和清空对话。

## 自定义后端接口

在设置中选择“自定义后端接口”，填写完整的 HTTP(S) 地址和可选模型名称、访问令牌。浏览器会直接发送 `POST` 请求，正文格式如下：

```json
{"message":"你好","model":"可选模型名","context":null,"history":[]}
```

连接到演示窗口时，`context` 会包含 `kind`、`title`、`path` 和 `description`。后端返回 `{"reply":"回复内容"}`。页面提供“测试接口”按钮；后端需要允许页面来源跨域访问。地址、模型和界面偏好保存在本地浏览器；访问令牌仅在当前页面内存中保留，不会保存。演示窗口仍是模拟内容，不会读取真实桌面窗口。

当前是交互原型：窗口、路径和文件均为演示数据，不会读取或修改本机真实窗口和文件。演示模式的 AI 回复也是预设文本；自定义接口模式会向你填写的后端发送消息和演示窗口上下文。真正的桌面接管需要原生窗口枚举、权限处理和文件系统桥接。

