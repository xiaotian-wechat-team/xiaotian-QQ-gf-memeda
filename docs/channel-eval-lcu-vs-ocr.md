# 小甜微信收发通道评估：LCU vs 截图 OCR

2026-10-24 凌晨（Asia/Shanghai）在 Mac mini 上只读实测。微信 4.1.15，窗口 880×640 pt，当前会话就是「小熊」（可见 1 个时间戳、1 条系统提示、2 条对方消息，暂时没有「我」发的消息）。全程没有点击、输入或发送；测试时前台应用是「系统设置」，微信在后台。

客户端：`tools/lcu_client.py`（stdio MCP，initialize → notifications/initialized → tools/call `js`；声明 elicitation 能力，收到 elicitation 一律 decline）。

## 1. 能读到什么

| 项 | LCU（getAXState） | OCR（mac_wechat_io） |
|---|---|---|
| 无障碍树内容 | **只有 11 个节点**：标准窗口、关闭/全屏/最小化按钮、菜单栏（微信/文件/编辑/显示/窗口/帮助）。会话列表、标题、消息、输入框、发送按钮都**没有暴露** | — |
| 会话标题 | 无（AX 里拿不到） | `in_chat_with()` 返回 True；OCR 结果是「。小熊」（🐻 被识别成「。」，但备注能匹配上） |
| 消息文本 | 无 | 4 条全部读到 |
| 发送方 | 无 | 2 条 them、2 条 system，判断正确；me 侧仍然没有样本 |
| 顺序 | 无 | 按 y 坐标排序，正确 |
| 截图 | `getAXStateAndScreenshot` 能给出窗口截图（880×640，1x，约 63 KB PNG），需要自己再跑 OCR | screencapture -l 窗口截图（880 px 宽） |

结论：微信 4.x 的无障碍树不暴露消息，**LCU 本身拿不到任何聊天文本**，读消息只能靠「LCU 截图 + OCR」，本质还是 OCR。

## 2. 准确率（同一画面，Apple Vision OCR）

- 消息正文：「我通过了你的朋友验证请求…开始聊天了」「你好呀」「以上是打招呼的消息」「19:35」，两种截图源都**逐字正确**。
- 已知错字都出在 UI 部件上，不影响消息：🐻 → 「。」，搜索框「搜索」→「搜案」，图标被识别成「⑨」「『」「=」之类的乱码。
- emoji：🐻 两边都识别不出来（所以现在才靠像素定位）。消息里的 emoji 还没有样本，按 Vision 的能力大概率会丢掉或认错。
- LCU 的截图是 1x，跟现有截图同分辨率，OCR 结果几乎一样，没有精度优势。

## 3. 速度（每项 3 次）

| 操作 | 耗时 |
|---|---|
| LCU 进程启动 + initialize | 0.19 s |
| LCU 首次 `getApp`（带约 24 KB 文档） | 0.88 s |
| LCU `getAXState` | 0.05–0.07 s（但内容为空） |
| LCU `getAXStateAndScreenshot` | 0.05–0.06 s（另外还要 OCR，约 0.1 s） |
| OCR `read_messages()` 全流程 | 首次 0.39 s，之后 0.19 s |
| 其中 capture / ocr | 0.08 s / 0.10 s |
| `in_chat_with()` | 0.18 s |

两边都在亚秒级，相对 10–30 s 的轮询间隔，速度不是决定因素。

## 4. 稳定性 / 打扰

- 读取：两边都**不用微信在前台**（实测前台是「系统设置」，读完前台也没变），都不动鼠标。前提是微信窗口不能最小化。
- 输入：OCR 通道发消息要用 cliclick 点击和 ⌘V，会抢鼠标和焦点，打断屏幕前的人。LCU 文档里只说 **Linux** 输入「不激活窗口、不移动指针」，Windows 会激活窗口；**macOS 没有承诺后台输入**。macOS 上 `paste` 会借用系统剪贴板，事后再恢复。
- 依赖风险：LCU 是 ChatGPT/Codex 的 Computer Use 组件（SkyComputerUseService），要单独授权（`lcu apps allow`，需要 Touch ID 或密码），还会随 `lcu update` 变化。首轮调用曾报「createElicitation is unavailable」，客户端声明 elicitation 能力后才正常返回。返回里还带着 Codex 的确认政策（发消息属于「每次都要确认」），无人值守用起来有摩擦。另外多一个可能卡死的服务进程（需要时 `pkill -f SkyComputerUseService`）。

## 5. 发送能力（只看文档，没有实操）

- `setValue(index)`、`click(index)`、`selectText` 都要 AX 元素索引，而微信的输入框和「发送」按钮**不在树里**，所以都用不了。
- 能用的只有坐标操作：`click([x,y])`、`paste(text)`（会恢复剪贴板）、`pressKey("Return")`。这和现有的 cliclick + pbcopy + 回车是一回事，只是剪贴板更干净。macOS 上是否在后台执行没有文档保证。

## 结论：继续用 OCR，不换 LCU；LCU 最多作为备选截图源

1. 在微信 4.1.15 上 LCU 的 AX 树只有窗口外框和菜单栏，消息、标题、输入框都是 0 暴露，它的核心优势（结构化文本、按元素 setValue）完全用不上。
2. 读取还是得 OCR，准确率跟现有 OCR 一样（消息逐字正确，🐻 同样识别不出）。
3. 速度上，OCR 一次读取 0.19 s，LCU 截图 0.05 s 加 OCR 0.1 s，差别可以忽略。
4. 换 LCU 会多出依赖（ChatGPT 组件、授权、更新、elicitation、确认政策），而 macOS 后台输入没有保证。
5. 可以考虑的混合用法：现有 `cliclick` 发送改用 LCU 的 `paste`（剪贴板会恢复），但要等真机试跑时再实测是否抢焦点。在那之前不改主流程。微信以后的版本如果开放了 AX 消息节点，用 `tools/lcu_client.py` 的 `getAXState` 很快就能复测。
